"""The run report.

Four things every import must say out loud, because each is a way the design can
fail silently:

* **counts by label and by relationship type** — the numbers that must not move on
  a re-import, and the numbers that must flatten as the file count grows;
* **the resolution rate** — how many declared semanticIds the dictionary knows.
  Around 69 % is the measured figure; ~31 % resolving nowhere is a finding, not a
  bug, so the unresolved ones are listed rather than hidden;
* **top-degree nodes** — a high-degree ``:Concept`` or ``:SemanticId`` is expected
  and healthy (543× Reference reuse has to land somewhere). A high-degree node
  reached by *instance* edges is the super-node this whole design exists to avoid,
  so the degrees are printed and not merely asserted about;
* **documents that failed to parse** — ingestion never crashes on bad input. A
  corpus with 5 000 truncated packages must load the other 155 000 and say so.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

TOP_DEGREE_LIMIT = 10
UNRESOLVED_SAMPLE = 25
#: How many rows of the idShort sections reach the console. The full lists always
#: reach ``--report-json``.
IDSHORT_SAMPLE = 25
#: A stem ratio over one or two shapes says nothing, so the ranked table starts here.
STEM_MIN_SHAPES = 4

RESOLUTION_QUERY_SIMPLE = """
MATCH (n:SemanticId)
WITH n, count { (n)-[:RESOLVES_TO]->(:Concept) } AS hits
RETURN count(n) AS total, sum(CASE WHEN hits > 0 THEN 1 ELSE 0 END) AS resolved
"""

#: Resolution restricted to semanticIds that an *instance* shape declares. The
#: measured 68.7 % is over the semanticIds vendors actually ship; a template-only
#: identifier (``https://admin-shell.io/idta/nameplate/3/0/Nameplate`` and its
#: kin) has no dictionary entry by construction and would drag the figure down
#: without saying anything about coverage of the corpus.
#:
#: Supplemental declarations count: a ``supplementalSemanticId`` is shipped by the
#: vendor exactly as the primary one is, and it is the *more* likely of the two to
#: name a dictionary concept.
RESOLUTION_USED_QUERY = """
MATCH (n:SemanticId)<-[:DECLARES|DECLARES_SUPPLEMENTAL]-(s:Shape)
WHERE coalesce(s.occurrences, 0) > 0
WITH DISTINCT n
WITH n, count { (n)-[:RESOLVES_TO]->(:Concept) } AS hits
RETURN count(n) AS total, sum(CASE WHEN hits > 0 THEN 1 ELSE 0 END) AS resolved
"""

#: How much of the "vocabulary" is really per-product identity.
#:
#: A shape is keyed on the ``idShort`` verbatim, which is right — normalising would
#: merge vocabularies that genuinely differ. But several vendors encode product data
#: in the ``idShort`` (``OrdNo_1234567``, ``Document07``, SICK's ``ZMN…`` codes), and
#: those shapes are not vocabulary at all: each occurs once, and the shape count then
#: tracks the product count instead of the schema. The graph is the only place this
#: is visible corpus-wide, so the import reports it rather than hiding it in a tail.
VOCABULARY_QUALITY_QUERY = """
MATCH (s:Shape) WHERE NOT s:SubmodelGroup
RETURN count(s) AS shapes,
       sum(CASE WHEN coalesce(s.occurrences, 0) = 1 THEN 1 ELSE 0 END) AS singletons,
       sum(CASE WHEN s.id_short =~ '.*[0-9]{2,}.*' THEN 1 ELSE 0 END) AS numbered,
       sum(CASE WHEN NOT coalesce(s.id_short, '') =~ '.*[0-9]{2,}.*'
                THEN 1 ELSE 0 END) AS plain
"""

NUMBERED_BY_VENDOR_QUERY = """
MATCH (v:Vendor)-[:USES]->(s:Shape)
WITH v, count(s) AS shapes,
     sum(CASE WHEN s.id_short =~ '.*[0-9]{2,}.*' THEN 1 ELSE 0 END) AS numbered
RETURN v.name AS vendor, shapes, numbered,
       toInteger(100.0 * numbered / shapes) AS pct
ORDER BY shapes DESC
"""

UNRESOLVED_QUERY = """
MATCH (n:SemanticId)
WHERE NOT (n)-[:RESOLVES_TO]->(:Concept)
RETURN n.value AS value,
       count { (n)<-[:DECLARES|DECLARES_SUPPLEMENTAL]-(:Shape) } AS shapes
ORDER BY shapes DESC, value ASC
LIMIT $limit
"""

#: Unresolved semanticIds grouped by the *form* of the identifier, because "3 337
#: resolve nowhere" is not one finding but several with different answers:
#:
#: * an IDTA or vendor URL names a template, not a concept — nothing to resolve;
#: * a plain IRDI absent from the loaded dictionary is a vendor citing a retired or
#:   unloaded segment — a real finding about the corpus;
#: * a SAMM aspect-model URN belongs to a different identifier system entirely;
#: * a compound ``A~0/B`` names B in the context of A and is deliberately not collapsed;
#: * an unjoinable wrapper is a *defect* — an IRDI the join key cannot see through.
#:
#: The last bucket is the one that should stay at zero. Printing the buckets means a new
#: bad form in a future corpus shows up as a bucket instead of hiding in a long tail.
UNRESOLVED_FORMS_QUERY = """
MATCH (n:SemanticId)
WHERE NOT (n)-[:RESOLVES_TO]->(:Concept)
WITH n, count { (n)<-[:DECLARES|DECLARES_SUPPLEMENTAL]-(:Shape) } AS shapes
WITH CASE
       // Keyed on the *base*, because that is what the join uses: a wrapper the key
       // can now read has an IRDI base and belongs under "absent", not "unreadable".
       WHEN n.value_base =~ '^[0-9]{4}[-/].*[~/].*' THEN 'compound IRDI (not collapsed)'
       WHEN n.value_base =~ '^[0-9]{4}[-/].*'    THEN 'IRDI absent from the dictionary'
       WHEN n.value_base =~ '(?i)^urn:irdi:.*'   THEN 'wrapper the join key cannot read'
       WHEN n.value_base =~ '(?i)^urn:samm:.*'   THEN 'SAMM aspect model (other system)'
       WHEN n.value_base =~ '(?i)^urn:.*'        THEN 'other URN (incl. misspelled wrapper)'
       WHEN n.value_base CONTAINS 'eclass-cdp.com' THEN 'ECLASS-CDP URL the key cannot read'
       WHEN n.value_base STARTS WITH 'http'      THEN 'IDTA / vendor URL (not a concept)'
       ELSE 'other'
     END AS form, shapes
RETURN form, count(*) AS values, sum(shapes) AS declared_by
ORDER BY values DESC
"""

TOP_DEGREE_QUERY = """
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN $labels)
RETURN labels(n) AS labels,
       coalesce(n.value, n.id, n.name, n.template_id, n.id_short, n.shape_key) AS key,
       count { (n)--() } AS degree
ORDER BY degree DESC
LIMIT $limit
"""

#: The layout half of the graph, and the honest accounting of what merging cost.
#:
#: ``:CONTAINS`` is the union of every submodel's layout, so a parent with several
#: children cannot say which combinations of them actually co-occur. What it *can*
#: say is how well supported each relation is, and that is what separates schema
#: from noise: a pair seen in twelve thousand submodels is real, one seen once may
#: be a typo. An edge with zero occurrences is declared by an IDTA template and
#: implemented by nobody.
CONTAINMENT_QUERY = """
MATCH ()-[e:CONTAINS]->()
RETURN count(e) AS edges,
       sum(CASE WHEN coalesce(e.vendors, 0) > 1 THEN 1 ELSE 0 END) AS multi_vendor,
       sum(CASE WHEN coalesce(e.submodels, 0) = 1 THEN 1 ELSE 0 END) AS seen_once,
       sum(CASE WHEN coalesce(e.in_template, false) THEN 1 ELSE 0 END) AS in_template,
       sum(CASE WHEN coalesce(e.occurrences, 0) = 0 THEN 1 ELSE 0 END) AS declared_only
"""

AMBIGUITY_QUERY = """
MATCH (p:Shape)-[e:CONTAINS]->()
WITH p, count(e) AS children
RETURN count(p) AS parents,
       sum(CASE WHEN children > 1 THEN 1 ELSE 0 END) AS branching,
       max(children) AS widest
"""

#: Which dictionary defines the graph, and how much of it any of them is actually
#: asked for. Two sources publish disjoint identifier spaces (ECLASS ``0173``, IEC
#: CDD ``0112``), so "we loaded a dictionary" and "that dictionary answers what the
#: vendors point at" are different questions and both are worth printing.
CONCEPTS_BY_SOURCE_QUERY = """
MATCH (c:Concept)
WITH c.source AS source, c.kind AS kind,
     count { (:SemanticId)-[:RESOLVES_TO]->(c) } AS hits
RETURN source, kind, count(*) AS concepts,
       sum(CASE WHEN hits > 0 THEN 1 ELSE 0 END) AS asked_for
ORDER BY concepts DESC
"""

#: What the supplemental edge bought. ``only`` is the number reachable *solely* as a
#: supplemental — semanticIds the graph could not see at all before, which is the
#: whole justification for the edge type.
#:
#: ``count {}`` is computed in a ``WITH`` before aggregating: left in the ``RETURN``
#: beside ``count(*)`` it silently becomes a grouping key and splits the result.
SUPPLEMENTAL_QUERY = """
MATCH (:Shape)-[e:DECLARES_SUPPLEMENTAL]->(n:SemanticId)
WITH n, count(e) AS shapes,
     count { (n)-[:RESOLVES_TO]->(:Concept) } AS hits,
     count { (n)<-[:DECLARES]-(:Shape) } AS primary
RETURN count(n) AS values, sum(shapes) AS edges,
       sum(CASE WHEN hits > 0 THEN 1 ELSE 0 END) AS resolved,
       sum(CASE WHEN primary = 0 THEN 1 ELSE 0 END) AS only,
       sum(CASE WHEN primary = 0 AND hits > 0 THEN 1 ELSE 0 END) AS only_resolved
"""

SUPPLEMENTAL_TOP_QUERY = """
MATCH (:Shape)-[e:DECLARES_SUPPLEMENTAL]->(n:SemanticId)
WITH n, count(e) AS shapes
RETURN n.value AS value, shapes,
       count { (n)-[:RESOLVES_TO]->(:Concept) } > 0 AS resolves
ORDER BY shapes DESC, value ASC
LIMIT $limit
"""

#: What normalising the idShort buys, and what it must **not** cost.
#:
#: A collapse of ~1.03x is the point: the derived forms are for matching, not for
#: merging, and a much harder collapse would mean the normaliser is destroying
#: distinctions the corpus really makes. The number is printed rather than asserted
#: so that a change to the normaliser shows up as a number moving.
IDSHORT_FORMS_QUERY = """
MATCH (s:Shape) WHERE s.id_short IS NOT NULL
RETURN count(DISTINCT s.id_short) AS raw,
       count(DISTINCT s.id_short_norm) AS norm,
       count(DISTINCT s.id_short_base) AS base
"""

#: The interoperability payoff: two vendors writing one concept two ways.
#: ``ValueID``/``ValueId``, ``CityTown``/``City_Town`` — no semanticId involved, and
#: nothing in the graph could see them as the same thing before.
IDSHORT_MERGED_QUERY = """
MATCH (s:Shape) WHERE s.id_short IS NOT NULL
WITH s.id_short_norm AS form, collect(DISTINCT s.id_short) AS spellings
WHERE size(spellings) > 1
RETURN form, spellings, size(spellings) AS spelling_count
ORDER BY spelling_count DESC, form ASC
"""

#: Cross-vendor bridges found without a dictionary. A vendor that ships no
#: semanticId at all is still joined to the rest of the corpus by these.
IDSHORT_CROSS_VENDOR_QUERY = """
MATCH (v:Vendor)-[:USES]->(s:Shape) WHERE s.id_short IS NOT NULL
WITH s.id_short_norm AS form, collect(DISTINCT v.name) AS vendors
WHERE size(vendors) > 1
RETURN form, vendors, size(vendors) AS vendor_count
ORDER BY vendor_count DESC, form ASC
"""

#: Junk-semanticId detection: **distinct ``id_short_base`` per declaring shape**.
#:
#: A semanticId declared by 204 shapes that carry 202 different stems is a
#: catch-all — the vendor is filing unrelated things under one identifier. One
#: declared by 73 shapes with 5 stems is an enumeration (``AAQ672-Connector__00__``
#: … ``__47__``), which is exactly what a shared identifier is *for*. The ratio
#: separates them; the raw in-degree does not.
#:
#: The ``0.5`` cut and the ``stems > 3`` floor were chosen by eye on one sample, so
#: this reports the whole ranked table and the distribution and leaves the threshold
#: to whoever reads it. A known false positive stays in the table rather than being
#: filtered away: a collection legitimately named after its content
#: (``Australia``, ``Austria``, ``Belgium`` …) scores 1.00 and is not misuse. Telling
#: those apart needs the ``:CONTAINS`` parent test, which this query does not do.
SEMANTIC_ID_STEMS_QUERY = """
MATCH (s:Shape)-[:DECLARES]->(n:SemanticId)
WHERE s.id_short_base IS NOT NULL
WITH n, count(s) AS shapes, count(DISTINCT s.id_short_base) AS stems
WHERE shapes >= $min_shapes
RETURN n.value AS value, shapes, stems,
       round(1.0 * stems / shapes, 3) AS ratio,
       count { (n)-[:RESOLVES_TO]->(:Concept) } > 0 AS resolves
ORDER BY ratio DESC, shapes DESC, value ASC
"""

#: The distribution behind the table above — ten buckets, so the shape of the data
#: sets the threshold instead of a constant chosen on one sample.
SEMANTIC_ID_STEM_HISTOGRAM_QUERY = """
MATCH (s:Shape)-[:DECLARES]->(n:SemanticId)
WHERE s.id_short_base IS NOT NULL
WITH n, count(s) AS shapes, count(DISTINCT s.id_short_base) AS stems
WHERE shapes >= $min_shapes
WITH CASE WHEN stems = shapes THEN 10
          ELSE toInteger(floor(10.0 * stems / shapes)) END AS bucket
RETURN bucket, count(*) AS semantic_ids
ORDER BY bucket ASC
"""

DECLARES_DEGREE_QUERY = """
MATCH (n:SemanticId)
RETURN n.value AS value, count { (n)<-[:DECLARES]-(:Shape) } AS shapes
ORDER BY shapes DESC
LIMIT $limit
"""


@dataclass
class RunReport:
    """Everything one ``vocab import`` did, printable and JSON-serialisable."""

    started: float = field(default_factory=time.time)
    seconds: float = 0.0
    files: int = 0
    documents: int = 0
    failed_documents: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    shells: int = 0
    submodels: int = 0
    elements: int = 0
    vendors: list[str] = field(default_factory=list)
    nodes: dict[str, int] = field(default_factory=dict)
    relationships: dict[str, int] = field(default_factory=dict)
    resolution: dict[str, Any] = field(default_factory=dict)
    resolution_used: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    containment: dict[str, Any] = field(default_factory=dict)
    ambiguity: dict[str, Any] = field(default_factory=dict)
    numbered_by_vendor: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    top_degree: list[dict] = field(default_factory=list)
    top_declares: list[dict] = field(default_factory=list)
    concepts_by_source: list[dict] = field(default_factory=list)
    supplemental: dict[str, Any] = field(default_factory=dict)
    supplemental_top: list[dict] = field(default_factory=list)
    unresolved_forms: list[dict] = field(default_factory=list)
    id_short_forms: dict[str, Any] = field(default_factory=dict)
    #: Full lists, not samples: the console prints the head, ``--report-json`` keeps
    #: everything. They are the evidence for the normaliser, and a sample cannot be
    #: checked against a corpus.
    id_short_merged: list[dict] = field(default_factory=list)
    id_short_cross_vendor: list[dict] = field(default_factory=list)
    semantic_id_stems: list[dict] = field(default_factory=list)
    semantic_id_stem_histogram: list[dict] = field(default_factory=list)
    write_seconds: dict[str, float] = field(default_factory=dict)
    rows_written: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def render(self) -> str:
        lines: list[str] = []
        add = lines.append
        add("=" * 72)
        add("vocabulary import")
        add("=" * 72)
        add(f"wall clock            {self.seconds:8.1f} s")
        add(f"files                 {self.files:8,}")
        add(f"documents parsed      {self.documents:8,}")
        add(f"documents failed      {self.failed_documents:8,}")
        add(f"shells seen           {self.shells:8,}")
        add(f"submodels seen        {self.submodels:8,}")
        add(f"elements seen         {self.elements:8,}")
        if self.vendors:
            add(f"vendors               {', '.join(self.vendors)}")

        add("")
        add("NODES")
        for label, count in sorted(self.nodes.items(), key=lambda kv: -kv[1]):
            add(f"  {label:<16} {count:12,}")
        if self.elements and self.nodes:
            total = sum(self.nodes.values())
            add(f"  {'TOTAL':<16} {total:12,}"
                f"   ({self.elements / max(total, 1):,.1f} elements per node)")

        add("")
        add("EDGES")
        for rel, count in sorted(self.relationships.items(), key=lambda kv: -kv[1]):
            add(f"  {rel:<21} {count:12,}")

        if self.resolution:
            total = self.resolution.get("total", 0)
            resolved = self.resolution.get("resolved", 0)
            rate = 100.0 * resolved / total if total else 0.0
            add("")
            add("SEMANTIC ID RESOLUTION")
            add(f"  {resolved:,} of {total:,} resolve into the dictionary ({rate:.1f} %)")
            add(f"  {total - resolved:,} resolve nowhere — listed below, not hidden")
            used_total = self.resolution_used.get("total", 0)
            used_resolved = self.resolution_used.get("resolved", 0)
            if used_total:
                used_rate = 100.0 * used_resolved / used_total
                add(f"  of those a vendor actually ships: {used_resolved:,} of "
                    f"{used_total:,} ({used_rate:.1f} %)")
            for row in self.unresolved:
                add(f"    {row['shapes']:>5} shapes  {row['value']}")

        if self.containment.get("edges"):
            edges = self.containment["edges"]
            add("")
            add("CONTAINMENT — THE MERGED LAYOUT")
            add(f"  {edges:,} :CONTAINS edges replace a node per layout")
            add(f"  {self.containment['multi_vendor']:,} are shipped by more than one vendor")
            add(f"  {self.containment['seen_once']:,} were seen in exactly one submodel "
                f"— support, not schema")
            add(f"  {self.containment['in_template']:,} are declared by an IDTA template, "
                f"of which {self.containment['declared_only']:,} no vendor implements")
            if self.ambiguity.get("parents"):
                add(f"  {self.ambiguity['branching']:,} of {self.ambiguity['parents']:,} "
                    f"parents have more than one child (widest: "
                    f"{self.ambiguity['widest']:,}) — the merge cannot say which")
                add("  combinations of those children actually co-occur; the support "
                    "counters are what is left to rank them by.")

        if self.unresolved_forms:
            add("")
            add("UNRESOLVED SEMANTIC IDS BY FORM")
            for row in self.unresolved_forms:
                add(f"  {row['form']:<38}{row['values']:>6} values"
                    f"{row['declared_by']:>8} declaring shapes")

        if self.supplemental.get("values"):
            add("")
            add("SUPPLEMENTAL SEMANTIC IDS — THE CROSS-DICTIONARY BRIDGE")
            add(f"  {self.supplemental['values']:,} distinct values on "
                f"{self.supplemental['edges']:,} :DECLARES_SUPPLEMENTAL edges, "
                f"{self.supplemental['resolved']:,} resolve")
            add(f"  {self.supplemental['only']:,} are reachable *only* as a supplemental "
                f"— invisible before this edge existed, "
                f"{self.supplemental['only_resolved']:,} of them resolve")
            for row in self.supplemental_top:
                mark = "->" if row["resolves"] else "  "
                add(f"    {row['shapes']:>5} shapes {mark} {row['value']}")

        if self.concepts_by_source:
            add("")
            add("DICTIONARY — WHERE THE CONCEPTS CAME FROM")
            for row in self.concepts_by_source:
                add(f"  {str(row['source']):<10} {str(row['kind']):<12}"
                    f"{row['concepts']:>10,} concepts"
                    f"{row['asked_for']:>9,} a semanticId points at")

        if self.quality.get("shapes"):
            shapes = self.quality["shapes"]
            add("")
            add("HOW MUCH OF THE VOCABULARY IS REALLY PER-PRODUCT IDENTITY")
            add(f"  {self.quality['plain']:,} of {shapes:,} shapes have an idShort with no "
                f"2+ digit run ({100.0 * self.quality['plain'] / shapes:.1f} %)")
            add(f"  {self.quality['numbered']:,} carry a number — OrdNo_1234567, "
                f"Document07, ZMN codes: product data written into the idShort")
            add(f"  {self.quality['singletons']:,} occur exactly once in the whole corpus")
            for row in self.numbered_by_vendor:
                add(f"    {row['vendor']:<18} {row['shapes']:>8,} shapes, "
                    f"{row['pct']:>3} % numbered")

        if self.id_short_forms.get("raw"):
            forms = self.id_short_forms
            raw, norm, base = forms["raw"], forms["norm"], forms["base"]
            add("")
            add("IDSHORT NORMALISATION — MATCHING WITHOUT A DICTIONARY")
            add(f"  {raw:,} raw idShorts collapse to {norm:,} normalised forms "
                f"({raw / max(norm, 1):.2f}x) and {base:,} bases "
                f"({raw / max(base, 1):.2f}x)")
            add("  A small collapse is the point: these are matching keys, not a merge.")
            add(f"  {len(self.id_short_merged):,} forms merge more than one raw spelling "
                f"— the interoperability payoff (full list in --report-json)")
            for row in self.id_short_merged[:IDSHORT_SAMPLE]:
                add(f"    {row['form'][:38]:<38} {', '.join(row['spellings'][:4])}")
            add(f"  {len(self.id_short_cross_vendor):,} forms are used by more than one "
                f"vendor — bridges found with no semanticId at all")
            for row in self.id_short_cross_vendor[:IDSHORT_SAMPLE]:
                add(f"    {row['form'][:38]:<38} {', '.join(row['vendors'])}")

        if self.semantic_id_stems:
            add("")
            add("SEMANTIC IDS BY STEM RATIO  (distinct id_short_base / declaring shapes)")
            add("  ratio  stems shapes  res  semanticId")
            for row in self.semantic_id_stems[:IDSHORT_SAMPLE]:
                add(f"  {row['ratio']:>5.2f} {row['stems']:>6,} {row['shapes']:>6,}"
                    f"  {'yes' if row['resolves'] else ' no'}  {row['value'][:52]}")
            if len(self.semantic_id_stems) > IDSHORT_SAMPLE:
                add(f"    … {len(self.semantic_id_stems) - IDSHORT_SAMPLE:,} more, "
                    f"full table in --report-json")
            if self.semantic_id_stem_histogram:
                add("  distribution — the threshold is the reader's to set, not a constant:")
                widest = max(r["semantic_ids"] for r in self.semantic_id_stem_histogram)
                for row in self.semantic_id_stem_histogram:
                    lo = row["bucket"] / 10.0
                    bar = "#" * max(1, round(40 * row["semantic_ids"] / max(widest, 1)))
                    add(f"    {lo:>4.1f}-{min(lo + 0.1, 1.0):<4.1f}"
                        f"{row['semantic_ids']:>6,}  {bar}")

        if self.top_declares:
            add("")
            add("SEMANTIC IDS BY :DECLARES IN-DEGREE  (junk detection)")
            for row in self.top_declares:
                add(f"  {row['shapes']:>5} shapes  {row['value']}")

        if self.top_degree:
            add("")
            add("TOP-DEGREE NODES")
            for row in self.top_degree:
                labels = ":".join(row["labels"])
                add(f"  {row['degree']:>8,}  :{labels:<12} {str(row['key'])[:70]}")

        if self.write_seconds:
            add("")
            add("WRITES")
            for name, seconds in sorted(self.write_seconds.items(),
                                        key=lambda kv: -kv[1]):
                rows = self.rows_written.get(name, 0)
                add(f"  {name:<20} {rows:10,} rows  {seconds:7.2f} s")

        if self.failures:
            add("")
            add(f"FAILED DOCUMENTS (first {len(self.failures)})")
            for path, error in self.failures:
                add(f"  {error}\n    {path}")
        add("=" * 72)
        return "\n".join(lines)


def collect(loader, report: RunReport, *, top: int = TOP_DEGREE_LIMIT,
            unresolved: int = UNRESOLVED_SAMPLE,
            min_shapes: int = STEM_MIN_SHAPES) -> RunReport:
    """Fill the graph-side half of the report from a live database."""
    from neo4aas.vocabulary.schema import VOCABULARY_LABELS

    report.nodes = loader.node_counts()
    report.relationships = loader.relationship_counts()
    row = loader.run(RESOLUTION_QUERY_SIMPLE)
    if row:
        report.resolution = {"total": row[0]["total"], "resolved": row[0]["resolved"]}
    used = loader.run(RESOLUTION_USED_QUERY)
    if used:
        report.resolution_used = {"total": used[0]["total"],
                                  "resolved": used[0]["resolved"]}
    containment = loader.run(CONTAINMENT_QUERY)
    if containment:
        report.containment = dict(containment[0])
    ambiguity = loader.run(AMBIGUITY_QUERY)
    if ambiguity:
        report.ambiguity = dict(ambiguity[0])
    quality = loader.run(VOCABULARY_QUALITY_QUERY)
    if quality:
        report.quality = dict(quality[0])
    report.numbered_by_vendor = [dict(r) for r in loader.run(NUMBERED_BY_VENDOR_QUERY)]
    report.unresolved = [dict(r) for r in
                         loader.run(UNRESOLVED_QUERY, limit=unresolved)]
    report.top_degree = [dict(r) for r in
                         loader.run(TOP_DEGREE_QUERY,
                                    labels=list(VOCABULARY_LABELS), limit=top)]
    report.top_declares = [dict(r) for r in loader.run(DECLARES_DEGREE_QUERY, limit=top)]
    report.concepts_by_source = [dict(r) for r in loader.run(CONCEPTS_BY_SOURCE_QUERY)]
    report.unresolved_forms = [dict(r) for r in loader.run(UNRESOLVED_FORMS_QUERY)]
    supplemental = loader.run(SUPPLEMENTAL_QUERY)
    if supplemental:
        report.supplemental = dict(supplemental[0])
    report.supplemental_top = [dict(r) for r in
                               loader.run(SUPPLEMENTAL_TOP_QUERY, limit=top)]
    forms = loader.run(IDSHORT_FORMS_QUERY)
    if forms:
        report.id_short_forms = dict(forms[0])
    report.id_short_merged = [dict(r) for r in loader.run(IDSHORT_MERGED_QUERY)]
    report.id_short_cross_vendor = [dict(r) for r in loader.run(IDSHORT_CROSS_VENDOR_QUERY)]
    report.semantic_id_stems = [dict(r) for r in
                                loader.run(SEMANTIC_ID_STEMS_QUERY, min_shapes=min_shapes)]
    report.semantic_id_stem_histogram = [
        dict(r) for r in loader.run(SEMANTIC_ID_STEM_HISTOGRAM_QUERY, min_shapes=min_shapes)]
    report.write_seconds = dict(loader.counts.seconds)
    report.rows_written = dict(loader.counts.written)
    return report
