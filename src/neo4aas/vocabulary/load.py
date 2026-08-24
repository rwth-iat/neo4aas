"""Writing the vocabulary graph. Every statement is a ``MERGE``; none is a ``CREATE``.

That is not a style preference — it is the property the whole importer is judged
on. Re-importing the same corpus must not change a single count, which requires
two things together: keys that are pure functions of their input
(:mod:`neo4aas.vocabulary.extract`) and writes that are idempotent (here).

Counters on a shared edge
-------------------------

Support is a property, not an edge — an edge per occurrence is exactly the
super-node this design exists to avoid. But a counter and idempotence pull
against each other: ``SET c = c + n`` doubles on re-import.

``:USES`` is easy, because there is one edge per vendor: the run *replaces* what
that vendor's edge says. ``:CONTAINS`` is not — a single edge
is shared by every vendor that ships the pair, so a run cannot add to a number it
has no way of telling apart from history, and it cannot replace the whole number
either without erasing the other vendors.

So the support is stored **per vendor in parallel arrays** and a run replaces its
own slot:

.. code-block:: text

    (:Shape)-[:CONTAINS {
        vendor_names:       ['festo', 'wago'],
        vendor_occurrences: [ 18422,   9013 ],
        vendor_submodels:   [  6140,   3004 ],
        occurrences: 27435, submodels: 9144,   -- reduce() sums, recomputed
        in_template: true                      -- an IDTA template declares this pair
    }]->(:Shape)

Eight vendors means eight slots. It costs almost nothing, it makes the write
idempotent, and it gives back the one thing the merged layout model otherwise
loses: *which vendor* nests this shape under that one, and how often.

Loading disjoint shards of one vendor in separate runs needs
``mode="accumulate"``, which adds instead of replacing and is consequently **not**
idempotent. Default is ``replace``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

from neo4aas.vocabulary.concepts import coded_name_parent
from neo4aas.vocabulary.extract import VocabularyRecords
from neo4aas.vocabulary.schema import (
    LEGACY_LABELS,
    LEGACY_RELATIONSHIPS,
    VOCABULARY_LABELS,
    VOCABULARY_RELATIONSHIPS,
    drop_legacy_statements,
    schema_statements,
)

#: Rows per write transaction. Large enough that the round-trip disappears, small
#: enough that one transaction's memory stays bounded on a laptop-sized heap.
DEFAULT_BATCH_SIZE = 10_000

#: Rows per transaction when deleting. Smaller than a write batch: a delete holds
#: the whole change set in transaction memory and edges outnumber nodes ~50:1.
CLEAR_BATCH_SIZE = 10_000

#: Concept properties a higher-precedence source overwrites and a lower one only
#: fills in when missing. ``id`` is the identity and never moves; ``id_base`` is a
#: pure function of it (:func:`neo4aas.core.utils.id_base`) and is therefore
#: **recomputed on every write**, not frozen on first sight — the incoming row always
#: carries the current answer, and freezing it means a change to what ``id_base``
#: normalises can never reach a node that already exists.
CONCEPT_FIELDS: tuple[str, ...] = (
    "source", "kind", "coded_name", "level", "preferred_name", "definition",
    "unit", "data_type", "deprecated",
)


def _concept_merge_clause() -> str:
    """The per-field precedence merge, written out because it must not need APOC.

    A dictionary export (``source='eclass'``) outranks a ConceptDescription copied
    into a vendor file (``source='aas'``): the higher-precedence row wins every
    field, a lower or equal one fills only what is still null. Equal precedence is
    *not* a win, which is what makes a second identical import change nothing.
    """
    assignments = ",\n        ".join(
        f"c.{f} = CASE WHEN wins THEN row.{f} ELSE coalesce(c.{f}, row.{f}) END"
        for f in CONCEPT_FIELDS
    )
    return f"""
    UNWIND $rows AS row
    MERGE (c:Concept {{id: row.id}})
    WITH c, row, coalesce(row.precedence, 0) > coalesce(c.precedence, 0) AS wins
    SET c.id_base = row.id_base,
        c.precedence = CASE WHEN wins THEN row.precedence ELSE c.precedence END,
        {assignments}
    """


def _vendor_slot_clause(rel: str, fields: Sequence[str], accumulate: bool) -> str:
    """A shared shape-to-shape edge whose support is kept per vendor.

    ``keep`` is every array position that is *not* this vendor's, so the write is
    "drop my old slot, append my new one" — replacing a number the run can
    actually account for, and leaving every other vendor's untouched. Re-running
    the same rows reproduces the same arrays element for element, which is what
    the idempotence test checks.

    ``range(0, -1)`` is the empty list, so the first write needs no special case.
    """
    def slot(f: str) -> str:
        if not accumulate:
            return f"e.vendor_{f} = [i IN keep | e.vendor_{f}[i]] + row.{f}"
        # Shards of one vendor loaded separately: add to whatever my slot held.
        mine = (f"coalesce(head([i IN range(0, size(names) - 1) "
                f"WHERE names[i] = row.vendor | e.vendor_{f}[i]]), 0)")
        return f"e.vendor_{f} = [i IN keep | e.vendor_{f}[i]] + (row.{f} + {mine})"

    sets = ",\n        ".join(slot(f) for f in fields)
    return f"""
    UNWIND $rows AS row
    MATCH (a:Shape {{shape_key: row.source}})
    MATCH (b:Shape {{shape_key: row.target}})
    MERGE (a)-[e:{rel}]->(b)
    WITH e, row, coalesce(e.vendor_names, []) AS names
    WITH e, row, names,
         [i IN range(0, size(names) - 1) WHERE names[i] <> row.vendor] AS keep
    SET e.vendor_names = [i IN keep | names[i]] + row.vendor,
        {sets}
    """


#: ``id_short_norm`` / ``id_short_base`` are derived at import, never at query time:
#: they are lookup keys (indexed in :mod:`neo4aas.vocabulary.schema`), and a Cypher
#: expression cannot reproduce the normaliser. The ``SET`` is unconditional, so a
#: re-import writes the same value rather than accumulating one.
MERGE_SHAPES = """
UNWIND $rows AS row
MERGE (s:Shape {shape_key: row.shape_key})
SET s.id_short = row.id_short,
    s.id_short_norm = row.id_short_norm,
    s.id_short_base = row.id_short_base,
    s.model_type = row.model_type,
    s.value_type = row.value_type,
    s.category = coalesce(row.category, s.category)
"""

MERGE_SEMANTIC_IDS = """
UNWIND $rows AS row
MERGE (n:SemanticId {value: row.value})
SET n.value_base = row.value_base,
    n.key_types = [t IN coalesce(n.key_types, []) WHERE NOT t IN row.key_types] + row.key_types,
    n.ref_types = [t IN coalesce(n.ref_types, []) WHERE NOT t IN row.ref_types] + row.ref_types
"""

MERGE_DECLARES = """
UNWIND $rows AS row
MATCH (s:Shape {shape_key: row.shape_key})
MATCH (n:SemanticId {value: row.value})
MERGE (s)-[:DECLARES]->(n)
"""

#: "This element is *also* that concept." Kept apart from ``:DECLARES`` so that what
#: an element **is** stays distinguishable from what it is additionally filed under —
#: and because a shape has exactly one primary semanticId (it is in the key) but any
#: number of supplemental ones.
MERGE_DECLARES_SUPPLEMENTAL = """
UNWIND $rows AS row
MATCH (s:Shape {shape_key: row.shape_key})
MATCH (n:SemanticId {value: row.value})
MERGE (s)-[:DECLARES_SUPPLEMENTAL]->(n)
"""

MERGE_VENDOR_USES = """
UNWIND $rows AS row
MERGE (v:Vendor {name: row.vendor})
WITH v, row
MATCH (s:Shape {shape_key: row.shape_key})
MERGE (v)-[u:USES]->(s)
SET u.occurrences = %s, u.top_level = %s
"""

#: Pairs an IDTA template declares. No vendor ships a template, so this edge
#: carries no support arrays — it is the yardstick, not a measurement. An edge that
#: exists only because of this write ends up with ``occurrences = 0``, which is
#: exactly the "declared but nobody implements it" finding.
MERGE_TEMPLATE_CONTAINS = """
UNWIND $rows AS row
MATCH (a:Shape {shape_key: row.source})
MATCH (b:Shape {shape_key: row.target})
MERGE (a)-[e:CONTAINS]->(b)
SET e.in_template = true
"""

MERGE_TEMPLATES = """
UNWIND $rows AS row
MERGE (t:Template {template_id: row.template_id})
SET t.family = row.family,
    t.version = row.version,
    t.status = row.status,
    t.source_path = row.source_path,
    t.submodel_id = row.submodel_id
"""

MERGE_TEMPLATE_SHAPES = """
UNWIND $rows AS row
MATCH (t:Template {template_id: row.template_id})
MATCH (s:Shape {shape_key: row.shape_key})
MERGE (t)-[:DECLARES_SHAPE]->(s)
"""

MERGE_BROADER_PREFIX = """
UNWIND $rows AS row
MATCH (child:Concept {id: row.id})
MATCH (parent:Concept) WHERE parent.coded_name = row.parent_coded_name
  AND parent.id <> child.id
MERGE (child)-[:BROADER]->(parent)
"""

#: A ``:BROADER`` edge between two concepts named by their version-stripped bases,
#: so every version of the child links to every version of the parent. Both
#: dictionaries feed it: ECLASS's ``described_by`` block, and IEC CDD's explicit
#: ``superclass_irdi`` and class/property references.
MERGE_BROADER_BASE = """
UNWIND $rows AS row
MATCH (child:Concept) WHERE child.id_base = row.child_base
MATCH (parent:Concept) WHERE parent.id_base = row.parent_base
  AND parent.id <> child.id
MERGE (child)-[:BROADER]->(parent)
"""

#: The join that makes the graph worth having: a declared identifier against the
#: dictionary, on the **version-stripped** base. Matching the full IRDI instead
#: costs roughly a factor of ten in resolution rate, because ECLASS bumps the
#: version on effectively every shared base between releases.
MERGE_RESOLVES_TO = """
MATCH (n:SemanticId)
WITH n WHERE n.value_base IS NOT NULL
MATCH (c:Concept) WHERE c.id_base = n.value_base
MERGE (n)-[:RESOLVES_TO]->(c)
"""

RECOMPUTE_SHAPE_COUNTERS = """
MATCH (s:Shape)
OPTIONAL MATCH (:Vendor)-[u:USES]->(s)
WITH s, sum(coalesce(u.occurrences, 0)) AS total,
     sum(coalesce(u.top_level, 0)) AS roots
SET s.occurrences = total, s.top_level = roots
"""

#: Totals are derived from the per-vendor arrays rather than written alongside
#: them, so they can never disagree with the slots they summarise.
RECOMPUTE_EDGE_SUPPORT = """
MATCH ()-[e:%s]->()
SET e.occurrences = reduce(t = 0, x IN coalesce(e.vendor_occurrences, []) | t + x),
    e.vendors = size(coalesce(e.vendor_names, []))
"""

RECOMPUTE_CONTAINS_SUBMODELS = """
MATCH ()-[e:CONTAINS]->()
SET e.submodels = reduce(t = 0, x IN coalesce(e.vendor_submodels, []) | t + x),
    e.in_template = coalesce(e.in_template, false)
"""


def _batched(rows: Sequence, size: int) -> Iterator[list]:
    for start in range(0, len(rows), size):
        yield list(rows[start:start + size])


@dataclass
class LoadCounts:
    """What one load wrote, per statement. Purely for the run report."""

    written: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)

    def record(self, name: str, rows: int, seconds: float) -> None:
        self.written[name] = self.written.get(name, 0) + rows
        self.seconds[name] = self.seconds.get(name, 0.0) + seconds


class VocabularyLoader:
    """Batched, idempotent writes of a :class:`VocabularyRecords` fold.

    Owns nothing but a driver — deliberately not a :class:`BaseNeo4JClient`, whose
    constructor installs the *instance* importer's schema. The two graphs share a
    database engine and nothing else.
    """

    def __init__(self, driver, database: str | None = None,
                 batch_size: int = DEFAULT_BATCH_SIZE, mode: str = "replace") -> None:
        if mode not in ("replace", "accumulate"):
            raise ValueError(f"mode must be 'replace' or 'accumulate', got {mode!r}")
        self.driver = driver
        self.database = database
        self.batch_size = batch_size
        self.mode = mode
        self.counts = LoadCounts()

    # -- plumbing ---------------------------------------------------------------

    def _session(self):
        return self.driver.session(database=self.database) if self.database \
            else self.driver.session()

    def run(self, query: str, **params: Any) -> list:
        with self._session() as session:
            return list(session.run(query, **params))

    def _write(self, name: str, query: str, rows: Sequence[dict]) -> None:
        if not rows:
            return
        self._write_stream(name, query, rows)

    def _write_stream(self, name: str, query: str, rows: Iterable[dict]) -> int:
        """Batch an *iterable* of rows into transactions without materialising it.

        The ECLASS path needs this: a full dictionary export is on the order of a
        million concept rows, and holding them as dicts alongside the parse tree is
        gigabytes for no reason.
        """
        started = time.perf_counter()
        written, batch = 0, []
        with self._session() as session:
            for row in rows:
                batch.append(row)
                if len(batch) >= self.batch_size:
                    session.execute_write(
                        lambda tx, b=batch: tx.run(query, rows=b).consume())
                    written += len(batch)
                    batch = []
            if batch:
                session.execute_write(lambda tx, b=batch: tx.run(query, rows=b).consume())
                written += len(batch)
        if written:
            self.counts.record(name, written, time.perf_counter() - started)
        return written

    # -- schema -----------------------------------------------------------------

    def create_schema(self) -> None:
        """Constraints and indexes. Idempotent; must run before the first write."""
        with self._session() as session:
            for statement in schema_statements():
                session.run(statement).consume()

    # -- instances --------------------------------------------------------------

    def load_records(self, records: VocabularyRecords) -> None:
        """Write one extraction fold: shapes, semanticIds, containment, vendors."""
        self.load_shapes(records)
        self.load_semantic_ids(records)
        self.load_vendor_edges(records)
        self.load_containment(records)
        self.load_concepts(records.concepts.values())

    def load_shapes(self, records: VocabularyRecords) -> None:
        rows = [
            {"shape_key": rec.shape.key, "id_short": rec.shape.id_short,
             "id_short_norm": rec.shape.id_short_norm,
             "id_short_base": rec.shape.id_short_base,
             "model_type": rec.shape.model_type, "value_type": rec.shape.value_type,
             "category": rec.category}
            for rec in records.shapes.values()
        ]
        self._write("Shape", MERGE_SHAPES, rows)

    def load_semantic_ids(self, records: VocabularyRecords) -> None:
        rows = [
            {"value": rec.value, "value_base": rec.value_base,
             "key_types": sorted(rec.key_types), "ref_types": sorted(rec.ref_types)}
            for rec in records.semantic_ids.values()
        ]
        self._write("SemanticId", MERGE_SEMANTIC_IDS, rows)
        declares = [
            {"shape_key": rec.shape.key, "value": rec.shape.semantic_id}
            for rec in records.shapes.values() if rec.shape.semantic_id
        ]
        self._write("DECLARES", MERGE_DECLARES, declares)
        supplemental = [
            {"shape_key": shape_key, "value": value}
            for shape_key, value in sorted(records.supplemental_declarations)
        ]
        self._write("DECLARES_SUPPLEMENTAL", MERGE_DECLARES_SUPPLEMENTAL, supplemental)

    def load_vendor_edges(self, records: VocabularyRecords) -> None:
        occurrences = "row.occurrences" if self.mode == "replace" \
            else "coalesce(u.occurrences, 0) + row.occurrences"
        top_level = "row.top_level" if self.mode == "replace" \
            else "coalesce(u.top_level, 0) + row.top_level"
        rows = [
            {"vendor": vendor, "shape_key": key, "occurrences": count,
             "top_level": records.vendor_roots.get((vendor, key), 0)}
            for (vendor, key), count in sorted(records.vendor_shapes.items())
        ]
        self._write("USES", MERGE_VENDOR_USES % (occurrences, top_level), rows)

    def load_containment(self, records: VocabularyRecords) -> None:
        """``:CONTAINS``, the merged layout.

        This is the layout, merged over every submodel in the corpus. It replaces
        the per-layout node the first version of this importer had — 78x fewer
        edges, at the cost of no longer being able to enumerate layouts. The
        per-vendor support arrays are what is left to reason with.
        """
        contains = [
            {"vendor": vendor, "source": parent, "target": child,
             "occurrences": count,
             "submodels": records.contains_submodels.get((vendor, parent, child), 0)}
            for (vendor, parent, child), count in sorted(records.contains.items())
        ]
        self._write("CONTAINS",
                    _vendor_slot_clause("CONTAINS", ("occurrences", "submodels"),
                                        self.mode == "accumulate"),
                    contains)

        template = [{"source": parent, "target": child}
                    for parent, child in sorted(records.template_contains)]
        self._write("CONTAINS(template)", MERGE_TEMPLATE_CONTAINS, template)

    # -- dictionary -------------------------------------------------------------

    def load_concepts(self, concepts: Iterable[dict]) -> int:
        """Merge concept rows, higher-precedence source winning field by field."""
        return self._write_stream("Concept", _concept_merge_clause(), concepts)

    def load_broader_prefix(self, concepts: Iterable[dict]) -> int:
        """ECLASS CodedName prefix hierarchy: ``27210101 -> 27210100 -> …``."""
        rows = (
            {"id": row["id"], "parent_coded_name": coded_name_parent(row["coded_name"])}
            for row in concepts if coded_name_parent(row.get("coded_name"))
        )
        return self._write_stream("BROADER(prefix)", MERGE_BROADER_PREFIX, rows)

    def load_broader_property_class(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Property → describing class, joined on the version-stripped IRDI base."""
        return self.load_broader_base_pairs(pairs, name="BROADER(property)")

    def load_broader_base_pairs(self, pairs: Iterable[tuple[str, str]],
                                name: str = "BROADER") -> int:
        """``(child_base, parent_base)`` → ``:BROADER``, deduplicated first.

        Both dictionaries repeat a pair across the releases they ship, and ECLASS
        carries millions of them, so writing a pair twice is free work.
        """
        rows = ({"child_base": child, "parent_base": parent}
                for child, parent in sorted(set(pairs)))
        return self._write_stream(name, MERGE_BROADER_BASE, rows)

    # -- templates --------------------------------------------------------------

    def load_templates(self, templates: Iterable[Any]) -> int:
        """``:Template`` nodes plus one ``:DECLARES_SHAPE`` per shape they declare."""
        rows, shape_rows = [], []
        for template in templates:
            rows.append({
                "template_id": template.template_id, "family": template.family,
                "version": template.version, "status": template.status,
                "source_path": template.source_path, "submodel_id": template.submodel_id,
            })
            shape_rows.extend({"template_id": template.template_id, "shape_key": key}
                              for key in sorted(template.shape_keys))
        self._write("Template", MERGE_TEMPLATES, rows)
        self._write("DECLARES_SHAPE", MERGE_TEMPLATE_SHAPES, shape_rows)
        return len(rows)

    # -- finalize ---------------------------------------------------------------

    def finalize(self) -> None:
        """Derive what is a function of what was written: resolution and totals.

        Run after every source has been loaded. All of these are set-based and
        recompute from scratch, so running them twice changes nothing and running
        them once after a partial load still leaves the graph consistent with what
        it holds.
        """
        for name, statement in (
            ("RESOLVES_TO", MERGE_RESOLVES_TO),
            ("Shape counters", RECOMPUTE_SHAPE_COUNTERS),
            ("CONTAINS support", RECOMPUTE_EDGE_SUPPORT % "CONTAINS"),
            ("CONTAINS submodels", RECOMPUTE_CONTAINS_SUBMODELS),
        ):
            started = time.perf_counter()
            with self._session() as session:
                session.execute_write(lambda tx, s=statement: tx.run(s).consume())
            self.counts.record(name, 0, time.perf_counter() - started)

    # -- inspection -------------------------------------------------------------

    def node_counts(self) -> dict[str, int]:
        return {label: self.run(f"MATCH (n:{label}) RETURN count(n) AS c")[0]["c"]
                for label in VOCABULARY_LABELS}

    def relationship_counts(self) -> dict[str, int]:
        return {rel: self.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")[0]["c"]
                for rel in VOCABULARY_RELATIONSHIPS}

    def clear(self) -> dict[str, int]:
        """Delete every node this importer owns. Other labels are left alone.

        Relationships go first, in their own batched transactions, and only then the
        nodes. ``DETACH DELETE`` on the nodes alone runs out of transaction memory:
        a popular ``:Shape`` sits in tens of thousands of containment relations, so
        a batch of 10 000 shapes asks one transaction to drop far more edges than it
        can hold. Once the edges are gone the nodes are isolated and cheap.

        Labels this importer *used* to own are removed as well, and so are the
        constraints and indexes it used to create under property names it no longer
        writes, so upgrading a database written by an earlier version needs no manual
        cleanup.
        """
        removed = {}
        with self._session() as session:
            for rel in VOCABULARY_RELATIONSHIPS + LEGACY_RELATIONSHIPS:
                session.run(
                    f"MATCH ()-[r:{rel}]->() CALL (r) {{ DELETE r }} "
                    f"IN TRANSACTIONS OF {CLEAR_BATCH_SIZE} ROWS").consume()
            for label in VOCABULARY_LABELS + LEGACY_LABELS:
                before = session.run(
                    f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
                session.run(
                    f"MATCH (n:{label}) CALL (n) {{ DETACH DELETE n }} "
                    f"IN TRANSACTIONS OF {CLEAR_BATCH_SIZE} ROWS").consume()
                removed[label] = before
            for statement in drop_legacy_statements():
                session.run(statement).consume()
        return removed
