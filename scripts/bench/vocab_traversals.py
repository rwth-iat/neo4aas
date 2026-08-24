"""The six traversals the vocabulary graph exists for, run and timed.

Each is one Cypher pattern. That is the point of the argument for a graph here:
these are variable-depth, multi-hop and set-shaped, so in SQL each becomes a
recursive CTE plus four or five joins, re-planned per query. None of them touches
an instance — the whole graph is ~360 k nodes regardless of corpus size.

    uv run python scripts/bench/vocab_traversals.py --uri bolt://localhost:7688
"""

from __future__ import annotations

import argparse
import os
import time

CROSS_VENDOR = """
MATCH (v1:Vendor)-[:USES]->(a:Shape)-[:DECLARES]->(:SemanticId)
      -[:RESOLVES_TO]->(c:Concept)
      <-[:RESOLVES_TO]-(:SemanticId)<-[:DECLARES]-(b:Shape)<-[:USES]-(v2:Vendor)
WHERE v1.name < v2.name AND a.id_short <> b.id_short AND c.preferred_name IS NOT NULL
RETURN c.id AS concept, c.preferred_name AS name,
       v1.name AS vendor_a, a.id_short AS field_a,
       v2.name AS vendor_b, b.id_short AS field_b
ORDER BY concept, vendor_a, vendor_b, field_a, field_b
LIMIT $limit
"""

#: Bounded at *every* hop, and ranked by specificity — both were learned the hard way.
#:
#: An ECLASS class describes hundreds of properties and each exists in several
#: releases, so one concept's sibling set reaches thousands and the shape set behind
#: it tens of thousands. Unbounded, this query took **151 s** to return the same ten
#: rows it now returns in under one.
#:
#: Ranking matters more than the speed. Ordering siblings by occurrence count puts
#: the properties that are in *every* class first — ``AddressOfAdditionalLink``,
#: ``Title``, ``Language`` — so the expansion looked like noise while being
#: structurally correct. Ordering by how *few* classes a sibling belongs to (and
#: entering through the narrowest parent class) surfaces the neighbours that mean
#: something: from ``RatedVoltage`` it finds ``BAH005-rated_voltage``,
#: ``Bemessungsspannung`` and ``ratedVoltage`` — the same concept spelled four ways
#: by four vendors, which is the entire point.
QUERY_EXPANSION = """
MATCH (seed:Shape)-[:DECLARES]->(:SemanticId)-[:RESOLVES_TO]->(c:Concept)
WHERE toLower(coalesce(seed.id_short, '')) CONTAINS $term
  AND coalesce(seed.occurrences, 0) > 0
WITH seed, c ORDER BY seed.occurrences DESC LIMIT 1
MATCH (c)-[:BROADER]->(p:Concept)
WITH seed, p LIMIT 60
WITH seed, p, count { (p)<-[:BROADER]-(:Concept) } AS breadth
ORDER BY breadth ASC LIMIT 1
MATCH (p)<-[:BROADER]-(sibling:Concept)
WITH seed, p, sibling, count { (sibling)-[:BROADER]->(:Concept) } AS classes
ORDER BY classes ASC LIMIT 120
MATCH (sibling)<-[:RESOLVES_TO]-(:SemanticId)<-[:DECLARES]-(found:Shape)
WHERE coalesce(found.occurrences, 0) > 0
WITH seed, p, found.id_short AS field, min(classes) AS classes,
     max(found.occurrences) AS occurrences,
     head(collect(DISTINCT sibling.preferred_name)) AS name
RETURN seed.id_short AS from_field, p.preferred_name AS via_class,
       field, name, classes AS in_eclass_classes, occurrences
ORDER BY classes ASC, occurrences DESC
LIMIT $limit
"""

JUNK_DETECTION = """
MATCH (n:SemanticId)<-[:DECLARES]-(s:Shape)
WITH n, count(DISTINCT s) AS shapes,
     count(DISTINCT s.id_short) AS distinct_id_shorts
RETURN n.value AS semantic_id, shapes, distinct_id_shorts
ORDER BY distinct_id_shorts DESC, shapes DESC
LIMIT $limit
"""

FACETING = """
MATCH (parent:Shape)-[:CONTAINS]->(seed:Shape)
WHERE seed.id_short = $field
MATCH (parent)-[e:CONTAINS]->(other:Shape)
WHERE other.id_short <> $field AND other.id_short IS NOT NULL
RETURN other.id_short AS also_present, other.model_type AS model_type,
       e.submodels AS submodels, size(coalesce(e.vendor_names, [])) AS vendors
ORDER BY submodels DESC, also_present
LIMIT $limit
"""

#: One hop, where the ``:Structure`` version needed two — siblings are now shapes
#: sharing a containment parent. Cheaper, and the support on the edge ranks them.
VENDOR_DISAGREEMENT = """
MATCH (p:Shape)-[e:CONTAINS]->(c:Shape)
WHERE size(coalesce(e.vendor_names, [])) > 1
RETURN p.id_short AS parent, c.id_short AS child,
       e.vendor_names AS vendors, e.vendor_submodels AS submodels_each,
       e.submodels AS submodels
ORDER BY size(e.vendor_names) DESC, submodels DESC
LIMIT $limit
"""

UNDECLARED_SUMMARY = """
MATCH ()-[e:CONTAINS]->()
WHERE coalesce(e.occurrences, 0) > 0
RETURN coalesce(e.in_template, false) AS declared, count(e) AS relations,
       sum(e.submodels) AS submodels
ORDER BY declared
"""

UNDECLARED_SAMPLE = """
MATCH (p:Shape)-[e:CONTAINS]->(c:Shape)
WHERE coalesce(e.occurrences, 0) > 0 AND NOT coalesce(e.in_template, false)
RETURN p.id_short AS parent, c.id_short AS child, c.model_type AS model_type,
       e.submodels AS submodels, e.vendor_names AS vendors
ORDER BY submodels DESC
LIMIT $limit
"""

DEPRECATED_VERSIONS = """
MATCH (v:Vendor)-[:USES]->(s:Shape)-[:DECLARES]->(n:SemanticId)
      -[:RESOLVES_TO]->(c:Concept)
WHERE c.source = 'eclass'
WITH v, n, c.id_base AS base
MATCH (release:Concept {id_base: base}) WHERE release.source = 'eclass'
WITH v, n, base, max(release.id) AS newest
WHERE n.value <> newest AND n.value STARTS WITH base
RETURN v.name AS vendor, count(DISTINCT n) AS stale_identifiers,
       collect(DISTINCT n.value)[..3] AS examples
ORDER BY stale_identifiers DESC
LIMIT $limit
"""

DEPRECATED_TEMPLATES = """
MATCH (t:Template {status: 'deprecated'})-[:DECLARES_SHAPE]->(s:Shape)<-[u:USES]-(v:Vendor)
RETURN v.name AS vendor, t.family AS deprecated_template, t.version AS version,
       count(DISTINCT s) AS shapes, sum(u.occurrences) AS occurrences
ORDER BY occurrences DESC
LIMIT $limit
"""

#: A field to seed the faceting traversal with, if it is present in the graph.
FACET_CANDIDATES = ("ManufacturerName", "URIOfTheProduct", "SerialNumber")

PICK_FACET = """
MATCH (s:Shape) WHERE s.id_short IN $candidates AND coalesce(s.occurrences, 0) > 0
RETURN s.id_short AS field ORDER BY s.occurrences DESC LIMIT 1
"""


def _cell(value: object, width: int = 46) -> str:
    """One table cell: single-line and bounded, so a long or multi-line value
    cannot shift every column to its right."""
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _run(session, title, why, query, **params):
    started = time.perf_counter()
    rows = [dict(r) for r in session.run(query, **params)]
    elapsed = (time.perf_counter() - started) * 1000
    print(f"\n{'-' * 78}\n{title}\n  {why}\n  {elapsed:.1f} ms, {len(rows)} rows")
    if not rows:
        print("  (no rows)")
        return
    columns = list(rows[0])
    cells = [[_cell(row[c]) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    for row in cells:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI",
                                                        "bolt://localhost:7687"))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", "12345678"))
    parser.add_argument("--term", default="voltage",
                        help="seed term for the query-expansion traversal")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    import neo4j

    driver = neo4j.GraphDatabase.driver(args.uri, auth=(args.user, args.password),
                                        notifications_min_severity="OFF")
    try:
        with driver.session() as session:
            picked = session.run(PICK_FACET, candidates=list(FACET_CANDIDATES)).single()
            field = picked["field"] if picked else FACET_CANDIDATES[0]

            _run(session, "1. CROSS-VENDOR FIELD MAPPING",
                 "two vendors, two names, one concept — 92 % of shapes are "
                 "vendor-private and this is the only bridge",
                 CROSS_VENDOR, limit=args.limit)
            _run(session, f"2. QUERY EXPANSION from '{args.term}'",
                 "term -> shape -> concept -> ECLASS siblings -> shapes",
                 QUERY_EXPANSION, term=args.term.lower(), limit=args.limit)
            _run(session, "3. JUNK DETECTION",
                 ":SemanticId by :DECLARES in-degree — a node degree, not a batch job",
                 JUNK_DETECTION, limit=args.limit)
            _run(session, f"4. FACETING around '{field}'",
                 "one hop: shapes sharing a containment parent, ranked by support",
                 FACETING, field=field, limit=args.limit)
            _run(session, "4b. WHERE VENDORS AGREE",
                 "the per-vendor support arrays: who nests this under that, how often",
                 VENDOR_DISAGREEMENT, limit=args.limit)
            _run(session, "5. UNDECLARED RELATIONS (summary)",
                 "containment a vendor ships that no IDTA template declares",
                 UNDECLARED_SUMMARY)
            _run(session, "5b. UNDECLARED RELATIONS (the biggest)",
                 "de-facto structure the industry uses and nobody published",
                 UNDECLARED_SAMPLE, limit=args.limit)
            _run(session, "6. DEPRECATED USAGE — superseded ECLASS releases",
                 "vendor -> shape -> concept -> a newer release of the same base",
                 DEPRECATED_VERSIONS, limit=args.limit)
            _run(session, "6b. DEPRECATED USAGE — deprecated IDTA templates",
                 "why deprecated/ is read: skipping it costs ~75 000 matches",
                 DEPRECATED_TEMPLATES, limit=args.limit)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
