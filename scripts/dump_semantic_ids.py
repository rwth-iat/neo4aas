#!/usr/bin/env python3
"""Dump every ``:SemanticId`` in a vocabulary graph, split by whether it resolved.

Two TSVs, written to a gitignored directory (``data/`` by default):

``resolved-semantic-ids.tsv``
    What the dictionary answers: the concept(s) each identifier names, their source,
    preferred name, unit and deprecation. Ranked by how many shapes declare it, so the
    head of the file is the vocabulary the corpus actually leans on.

``unresolved-semantic-ids.tsv``
    What it does not, bucketed by the *form* of the identifier — because "resolves
    nowhere" is not one finding. A vendor URL names a template and never had a concept
    to find; an IRDI absent from the loaded dictionary is a fact about the corpus; a
    wrapper the join key cannot read is a defect in this importer. Only the last kind
    is actionable, and it is worth being able to see it separately.

Both carry the declaring side (``primary``/``supplemental`` edge counts, whether a
vendor actually ships the declaring shape, one example element), so a row says who
asked for the identifier and not merely that it exists.

Usage:
    uv run python scripts/dump_semantic_ids.py [--uri bolt://localhost:7688] [--out-dir data]
"""

from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

import neo4j

#: The declaring side, shared by both dumps: who asked for this identifier, how, and
#: whether the shape that declares it is one a vendor actually ships (a template-only
#: declaration has ``occurrences = 0``).
_DECLARERS = """
OPTIONAL MATCH (sh:Shape)-[e:DECLARES|DECLARES_SUPPLEMENTAL]->(n)
WITH n, %s
     count(e)                                                           AS shapes,
     sum(CASE WHEN type(e) = 'DECLARES' THEN 1 ELSE 0 END)              AS primary,
     sum(CASE WHEN type(e) = 'DECLARES_SUPPLEMENTAL' THEN 1 ELSE 0 END) AS supplemental,
     sum(CASE WHEN coalesce(sh.occurrences, 0) > 0 THEN 1 ELSE 0 END)   AS shipped,
     collect(sh)[0]                                                     AS sample
"""

RESOLVED_QUERY = """
MATCH (n:SemanticId)-[:RESOLVES_TO]->(c:Concept)
WITH n, collect(c) AS concepts
""" + _DECLARERS % "concepts," + """
RETURN n.value AS value, n.base AS base,
       size(concepts)                                            AS concepts,
       [x IN concepts | x.source]                                AS sources,
       [x IN concepts | x.kind]                                  AS kinds,
       [x IN concepts WHERE x.preferred_name IS NOT NULL | x.preferred_name][0] AS preferred_name,
       [x IN concepts WHERE x.unit IS NOT NULL | x.unit][0]      AS unit,
       [x IN concepts WHERE x.data_type IS NOT NULL | x.data_type][0] AS data_type,
       any(x IN concepts WHERE x.deprecated)                     AS deprecated,
       shapes, primary, supplemental, shipped,
       sample.id_short AS element, sample.model_type AS model_type
ORDER BY shapes DESC, value ASC
"""

UNRESOLVED_QUERY = """
MATCH (n:SemanticId)
WHERE NOT (n)-[:RESOLVES_TO]->(:Concept)
""" + _DECLARERS % "" + """
RETURN n.value AS value, n.base AS base, shapes, primary, supplemental, shipped,
       sample.id_short AS element, sample.model_type AS model_type
ORDER BY shapes DESC, value ASC
"""

RESOLVED_COLUMNS = ["value", "base", "concepts", "sources", "kinds", "preferred_name",
                    "unit", "data_type", "deprecated", "shapes", "primary",
                    "supplemental", "shipped", "element", "model_type"]

UNRESOLVED_COLUMNS = ["form", "value", "base", "shapes", "primary", "supplemental",
                      "shipped", "element", "model_type"]

#: Why an identifier resolved nowhere. Keyed on the **base**, not the raw value: the
#: base is what the join actually compares, so a wrapper the key can now read falls
#: under "absent from the dictionary" rather than staying labelled unreadable.
FORMS: tuple[tuple[str, str], ...] = (
    ("compound-irdi-not-collapsed", r"^[0-9]{4}[-/].*[~/].*"),
    ("irdi-absent-from-dictionary", r"^[0-9]{4}[-/].*"),
    ("wrapper-unreadable", r"(?i)^urn:irdi:.*"),
    ("samm-aspect-model", r"(?i)^urn:samm:.*"),
    ("other-urn-or-typo", r"(?i)^urn:.*"),
    ("idta-or-vendor-url", r"^https?://.*"),
)

FORM_NOTES = (
    ("wrapper-unreadable", "an IRDI the join key cannot read — a defect in this importer"),
    ("other-urn-or-typo", "misspelled wrapper; deliberately not repaired"),
    ("compound-irdi-not-collapsed", "A~0/B is B in the context of A; kept whole on purpose"),
    ("irdi-absent-from-dictionary", "vendor cites an IRDI no loaded dictionary defines"),
    ("samm-aspect-model", "Eclipse SAMM id — a different identifier system"),
    ("idta-or-vendor-url", "names a template, not a concept — nothing to resolve"),
    ("other", "malformed: leaked key type, truncated URL, placeholder text"),
)


def form_of(base: str) -> str:
    for name, pattern in FORMS:
        if re.match(pattern, base):
            return "eclass-cdp-url-unreadable" if (
                name == "idta-or-vendor-url" and "eclass-cdp.com" in base) else name
    return "other"


def write(path: Path, columns: list[str], rows: list[dict], header: list[str]) -> None:
    with path.open("w") as out:
        for line in header:
            out.write(f"# {line}\n" if line else "#\n")
        out.write("\t".join(columns) + "\n")
        for row in rows:
            out.write("\t".join(
                "" if row.get(c) is None else str(row[c]) for c in columns) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="bolt://localhost:7688")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="12345678")
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    driver = neo4j.GraphDatabase.driver(
        args.uri, auth=(args.user, args.password), notifications_min_severity="OFF")
    try:
        with driver.session() as session:
            resolved = session.run(RESOLVED_QUERY).data()
            unresolved = session.run(UNRESOLVED_QUERY).data()
            total = session.run(
                "MATCH (n:SemanticId) RETURN count(n) AS c").single()["c"]
            sources = {r["s"]: r["c"] for r in session.run(
                "MATCH (c:Concept) RETURN c.source AS s, count(*) AS c")}
    finally:
        driver.close()

    # Deduplicated and joined here rather than in Cypher: the vocabulary graph is
    # deliberately APOC-free, so a dump of it must not need APOC either.
    for row in resolved:
        for key in ("sources", "kinds"):
            row[key] = ",".join(sorted({v for v in row[key] or () if v}))

    for row in unresolved:
        row["form"] = form_of(row["base"] or row["value"])

    stamp = datetime.date.today().isoformat()
    provenance = [
        f"Generated {stamp} from {args.uri} by scripts/dump_semantic_ids.py",
        f"{total:,} :SemanticId — {len(resolved):,} resolved "
        f"({100 * len(resolved) / max(total, 1):.1f} %), {len(unresolved):,} not.",
        f"Dictionary loaded: {', '.join(f'{k} {v:,}' for k, v in sorted(sources.items()))}.",
        "",
        "primary/supplemental = :DECLARES / :DECLARES_SUPPLEMENTAL edge counts;",
        "shipped = declaring shapes a vendor actually ships (occurrences > 0);",
        "element = one example declarer.",
        "",
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write(args.out_dir / "resolved-semantic-ids.tsv", RESOLVED_COLUMNS, resolved,
          ["Resolved semanticIds — declared in the graph and matched to a :Concept.",
           "One row per identifier; `concepts` counts every version it matched, since the",
           "join is on the version-stripped base and a dictionary ships many versions.",
           ""] + provenance)

    buckets: dict[str, list[int]] = {}
    for row in unresolved:
        bucket = buckets.setdefault(row["form"], [0, 0])
        bucket[0] += 1
        bucket[1] += row["shapes"]
    counts = [f"  {name:<30}{n:>6} values{sh:>8} declaring shapes"
              for name, (n, sh) in sorted(buckets.items(), key=lambda kv: -kv[1][0])]

    write(args.out_dir / "unresolved-semantic-ids.tsv", UNRESOLVED_COLUMNS, unresolved,
          ["Unresolved semanticIds — declared in the graph, matching no :Concept.",
           "Unresolved is not always a defect; the `form` column says which kind it is:",
           ""] + [f"  {name:<30}{note}" for name, note in FORM_NOTES] + [
           "", "Counts by bucket:"] + counts + [""] + provenance)

    print(f"resolved   {len(resolved):>6,} -> {args.out_dir / 'resolved-semantic-ids.tsv'}")
    print(f"unresolved {len(unresolved):>6,} -> {args.out_dir / 'unresolved-semantic-ids.tsv'}")
    for line in counts:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
