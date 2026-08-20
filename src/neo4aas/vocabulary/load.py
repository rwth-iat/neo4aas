"""Writing the vocabulary graph. Every statement is a ``MERGE``; none is a ``CREATE``.

That is not a style preference — it is the property the whole importer is judged
on. Re-importing the same corpus must not change a single count, which requires
two things together: keys that are pure functions of their input
(:mod:`neo4aas.vocabulary.extract`) and writes that are idempotent (here).

Occurrence counters
-------------------

``occurrences`` is a property, not an edge — an edge per occurrence is exactly the
super-node this design exists to avoid. But a counter and idempotence pull against
each other: ``SET c = c + n`` doubles on re-import.

The counter is therefore **scoped to the vendor** and *replaced*, not accumulated:
one run over a vendor's files sets ``(:Vendor)-[:USES]->(:Shape).occurrences`` to
what that run saw, and ``Shape.occurrences`` is recomputed from the sum over
vendors in :meth:`VocabularyLoader.finalize`. Re-importing the same files is then
a no-op, and re-importing *more* files of the same vendor gives the new total.

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
    VOCABULARY_LABELS,
    VOCABULARY_RELATIONSHIPS,
    schema_statements,
)

#: Rows per write transaction. Large enough that the round-trip disappears, small
#: enough that one transaction's memory stays bounded on a laptop-sized heap.
DEFAULT_BATCH_SIZE = 10_000

#: Rows per transaction when deleting. Smaller than a write batch: a delete holds
#: the whole change set in transaction memory and edges outnumber nodes ~100:1.
CLEAR_BATCH_SIZE = 10_000

#: Concept properties a higher-precedence source overwrites and a lower one only
#: fills in when missing. ``irdi``/``irdi_base`` are the identity and never move.
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
    MERGE (c:Concept {{irdi: row.irdi}})
    WITH c, row, coalesce(row.precedence, 0) > coalesce(c.precedence, 0) AS wins
    SET c.irdi_base = coalesce(c.irdi_base, row.irdi_base),
        c.precedence = CASE WHEN wins THEN row.precedence ELSE c.precedence END,
        {assignments}
    """


MERGE_SHAPES = """
UNWIND $rows AS row
MERGE (s:Shape {shape_key: row.shape_key})
SET s.id_short = row.id_short,
    s.model_type = row.model_type,
    s.value_type = row.value_type,
    s.category = coalesce(row.category, s.category)
"""

MERGE_SEMANTIC_IDS = """
UNWIND $rows AS row
MERGE (n:SemanticId {value: row.value})
SET n.base = row.base,
    n.key_types = [t IN coalesce(n.key_types, []) WHERE NOT t IN row.key_types] + row.key_types,
    n.ref_types = [t IN coalesce(n.ref_types, []) WHERE NOT t IN row.ref_types] + row.ref_types
"""

MERGE_DECLARES = """
UNWIND $rows AS row
MATCH (s:Shape {shape_key: row.shape_key})
MATCH (n:SemanticId {value: row.value})
MERGE (s)-[:DECLARES]->(n)
"""

MERGE_STRUCTURES = """
UNWIND $rows AS row
MERGE (st:Structure {structure_key: row.structure_key})
SET st.element_count = row.element_count,
    st.slot_count = row.slot_count,
    st.slots_written = coalesce(st.slots_written, false)
"""

#: The one ``CREATE`` in this importer, and the reason it is one.
#:
#: A structure's slots are written exactly once, in the same transaction that sets
#: the flag suppressing them — so the pair is atomic, a re-import writes nothing,
#: and the idempotence property the ``MERGE``-everywhere rule exists to guarantee
#: is preserved and tested (``test_reimport_changes_nothing``,
#: ``test_structure_slots_are_written_once``).
#:
#: ``MERGE`` here is not merely slower, it is **quadratic**: merging a relationship
#: with properties re-reads the source node's relationship chain per row, so a
#: 3 000-slot structure costs ~4.5 M chain steps to write 3 000 edges, and the
#: corpus has tens of thousands of such structures. ``CREATE`` is safe precisely
#: because ``structure_key`` *is* the ordered slot sequence: a structure's slots
#: cannot change, so there is never an existing edge to merge with.
CREATE_SLOTS = """
UNWIND $rows AS row
MATCH (st:Structure {structure_key: row.structure_key})
MATCH (sh:Shape {shape_key: row.shape_key})
CREATE (st)-[:HAS_SLOT {ord: row.ord, depth: row.depth, parent_ord: row.parent_ord}]->(sh)
"""

MARK_SLOTS_WRITTEN = """
UNWIND $keys AS key
MATCH (st:Structure {structure_key: key})
SET st.slots_written = true
"""

STRUCTURES_WITH_SLOTS = """
UNWIND $keys AS key
MATCH (st:Structure {structure_key: key})
WHERE st.slots_written
RETURN st.structure_key AS structure_key
"""

MERGE_VENDOR_USES = """
UNWIND $rows AS row
MERGE (v:Vendor {name: row.vendor})
WITH v, row
MATCH (s:Shape {shape_key: row.shape_key})
MERGE (v)-[u:USES]->(s)
SET u.occurrences = %s
"""

MERGE_VENDOR_SHIPS = """
UNWIND $rows AS row
MERGE (v:Vendor {name: row.vendor})
WITH v, row
MATCH (st:Structure {structure_key: row.structure_key})
MERGE (v)-[s:SHIPS]->(st)
SET s.submodels = %s
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
MATCH (child:Concept {irdi: row.irdi})
MATCH (parent:Concept) WHERE parent.coded_name = row.parent_coded_name
  AND parent.irdi <> child.irdi
MERGE (child)-[:BROADER]->(parent)
"""

MERGE_BROADER_PROPERTY_CLASS = """
UNWIND $rows AS row
MATCH (p:Concept) WHERE p.irdi_base = row.property_base
MATCH (c:Concept) WHERE c.irdi_base = row.class_base AND c.irdi <> p.irdi
MERGE (p)-[:BROADER]->(c)
"""

#: The join that makes the graph worth having: a declared identifier against the
#: dictionary, on the **version-stripped** base. Matching the full IRDI instead
#: costs roughly a factor of ten in resolution rate, because ECLASS bumps the
#: version on effectively every shared base between releases.
MERGE_RESOLVES_TO = """
MATCH (n:SemanticId)
WITH n WHERE n.base IS NOT NULL
MATCH (c:Concept) WHERE c.irdi_base = n.base
MERGE (n)-[:RESOLVES_TO]->(c)
"""

RECOMPUTE_SHAPE_OCCURRENCES = """
MATCH (s:Shape)
OPTIONAL MATCH (:Vendor)-[u:USES]->(s)
WITH s, sum(coalesce(u.occurrences, 0)) AS total
SET s.occurrences = total
"""

RECOMPUTE_STRUCTURE_OCCURRENCES = """
MATCH (st:Structure)
OPTIONAL MATCH (:Vendor)-[s:SHIPS]->(st)
WITH st, sum(coalesce(s.submodels, 0)) AS total
SET st.occurrences = total
"""


def _create_slots_tx(tx, rows: list[dict], keys: list[str]) -> None:
    """Create a batch of slots and mark their structures done, atomically."""
    tx.run(CREATE_SLOTS, rows=rows).consume()
    tx.run(MARK_SLOTS_WRITTEN, keys=keys).consume()


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
        """Write one extraction fold: shapes, semanticIds, structures, vendors."""
        self.load_shapes(records)
        self.load_semantic_ids(records)
        self.load_structures(records)
        self.load_vendor_edges(records)
        self.load_concepts(records.concepts.values())

    def load_shapes(self, records: VocabularyRecords) -> None:
        rows = [
            {"shape_key": rec.shape.key, "id_short": rec.shape.id_short,
             "model_type": rec.shape.model_type, "value_type": rec.shape.value_type,
             "category": rec.category}
            for rec in records.shapes.values()
        ]
        self._write("Shape", MERGE_SHAPES, rows)

    def load_semantic_ids(self, records: VocabularyRecords) -> None:
        rows = [
            {"value": rec.value, "base": rec.base,
             "key_types": sorted(rec.key_types), "ref_types": sorted(rec.ref_types)}
            for rec in records.semantic_ids.values()
        ]
        self._write("SemanticId", MERGE_SEMANTIC_IDS, rows)
        declares = [
            {"shape_key": rec.shape.key, "value": rec.shape.semantic_id}
            for rec in records.shapes.values() if rec.shape.semantic_id
        ]
        self._write("DECLARES", MERGE_DECLARES, declares)

    def load_structures(self, records: VocabularyRecords) -> None:
        rows = [
            {"structure_key": rec.structure.key,
             "element_count": rec.structure.element_count,
             "slot_count": rec.structure.slot_count}
            for rec in records.structures.values()
        ]
        self._write("Structure", MERGE_STRUCTURES, rows)

        # Slots are the bulk of the write and never change for a given structure key
        # — the key *is* the ordered slot sequence — so a structure whose slots are
        # already in the graph is skipped. This is what makes a re-import cheap as
        # well as idempotent, and it is what lets the slots be CREATEd (see
        # CREATE_SLOTS) instead of merged one quadratic chain-scan at a time.
        done: set[str] = set()
        for batch in _batched([row["structure_key"] for row in rows], self.batch_size):
            done.update(r["structure_key"]
                        for r in self.run(STRUCTURES_WITH_SLOTS, keys=batch))
        self._write_slots([rec.structure for rec in records.structures.values()
                           if rec.structure.key not in done])

    def _write_slots(self, structures: Sequence) -> None:
        """Write whole structures' slots, never splitting one across transactions.

        Each transaction creates the slots *and* marks the structures done, so a
        failure mid-load leaves a structure either fully slotted or not slotted at
        all — which is what makes the ``CREATE`` safe to retry.
        """
        started = time.perf_counter()
        written, rows, keys = 0, [], []

        def flush() -> None:
            nonlocal written, rows, keys
            if not rows:
                return
            with self._session() as session:
                session.execute_write(_create_slots_tx, rows, keys)
            written += len(rows)
            rows, keys = [], []

        for structure in structures:
            keys.append(structure.key)
            rows.extend(
                {"structure_key": structure.key, "shape_key": slot.shape_key,
                 "ord": slot.ord, "depth": slot.depth, "parent_ord": slot.parent_ord}
                for slot in structure.slots)
            if len(rows) >= self.batch_size:
                flush()
        flush()
        if written:
            self.counts.record("HAS_SLOT", written, time.perf_counter() - started)

    def load_vendor_edges(self, records: VocabularyRecords) -> None:
        occurrences = "row.occurrences" if self.mode == "replace" \
            else "coalesce(u.occurrences, 0) + row.occurrences"
        submodels = "row.submodels" if self.mode == "replace" \
            else "coalesce(s.submodels, 0) + row.submodels"
        uses = [{"vendor": vendor, "shape_key": key, "occurrences": count}
                for (vendor, key), count in sorted(records.vendor_shapes.items())]
        ships = [{"vendor": vendor, "structure_key": key, "submodels": count}
                 for (vendor, key), count in sorted(records.vendor_structures.items())]
        self._write("USES", MERGE_VENDOR_USES % occurrences, uses)
        self._write("SHIPS", MERGE_VENDOR_SHIPS % submodels, ships)

    # -- dictionary -------------------------------------------------------------

    def load_concepts(self, concepts: Iterable[dict]) -> int:
        """Merge concept rows, higher-precedence source winning field by field."""
        return self._write_stream("Concept", _concept_merge_clause(), concepts)

    def load_broader_prefix(self, concepts: Iterable[dict]) -> int:
        """ECLASS CodedName prefix hierarchy: ``27210101 -> 27210100 -> …``."""
        rows = (
            {"irdi": row["irdi"], "parent_coded_name": coded_name_parent(row["coded_name"])}
            for row in concepts if coded_name_parent(row.get("coded_name"))
        )
        return self._write_stream("BROADER(prefix)", MERGE_BROADER_PREFIX, rows)

    def load_broader_property_class(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Property → describing class, joined on the version-stripped IRDI base.

        Deduplicated first: ECLASS repeats a class→property pair across releases, and
        the dictionary carries millions of these, so writing them twice is free work.
        """
        rows = ({"property_base": p, "class_base": c} for p, c in sorted(set(pairs)))
        return self._write_stream("BROADER(property)", MERGE_BROADER_PROPERTY_CLASS, rows)

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
        """Derive what is a function of what was written: resolution and counters.

        Run after every source has been loaded. All three statements are set-based
        and recompute from scratch, so running them twice changes nothing and
        running them once after a partial load still leaves the graph consistent
        with what it holds.
        """
        for name, statement in (
            ("RESOLVES_TO", MERGE_RESOLVES_TO),
            ("Shape.occurrences", RECOMPUTE_SHAPE_OCCURRENCES),
            ("Structure.occurrences", RECOMPUTE_STRUCTURE_OCCURRENCES),
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
        a popular ``:Shape`` sits in tens of thousands of structures, so a batch of
        10 000 shapes asks one transaction to drop a hundred million edges. Once the
        edges are gone the nodes are isolated and deleting them is trivial.
        """
        removed = {}
        with self._session() as session:
            for rel in VOCABULARY_RELATIONSHIPS:
                session.run(
                    f"MATCH ()-[r:{rel}]->() CALL (r) {{ DELETE r }} "
                    f"IN TRANSACTIONS OF {CLEAR_BATCH_SIZE} ROWS").consume()
            for label in VOCABULARY_LABELS:
                before = session.run(
                    f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
                session.run(
                    f"MATCH (n:{label}) CALL (n) {{ DETACH DELETE n }} "
                    f"IN TRANSACTIONS OF {CLEAR_BATCH_SIZE} ROWS").consume()
                removed[label] = before
        return removed
