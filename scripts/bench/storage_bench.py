"""Measure the Neo4j footprint of a corpus tier under different storage models.

Usage:
    uv run python scripts/bench/storage_bench.py --tier t100 --variant current
    uv run python scripts/bench/storage_bench.py --tier t1k --variant nodedup,current

Each variant runs against a *fresh* Neo4j container (see neo4j_box) and reports node,
relationship, property and on-disk counts, so variants are comparable to the byte.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench import neo4j_box
from bench.corpus import LoadStats, iter_environments, tier_rows

from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG, AASNeo4JClient

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("bench")

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "bench"

# --- variants ---------------------------------------------------------------------

_NO_UNIQUE = [c for c in AAS_NEO4J_MODEL_CONFIG.default_optimization_clauses
              if "identifiable_id" not in c]


def config_for(variant: str):
    """Return the Neo4jModelConfig for a variant name."""
    if variant == "current":
        return AAS_NEO4J_MODEL_CONFIG
    if variant == "nodedup":
        # No dedup at all: every Reference and every repeated Identifiable becomes its own
        # node. The id-uniqueness constraint has to go with it — a repeated Submodel id
        # would otherwise abort the import instead of measuring it.
        return dataclasses.replace(
            AAS_NEO4J_MODEL_CONFIG,
            deduplicated_object_types=set(),
            deduplicated_by_id=set(),
            default_optimization_clauses=_NO_UNIQUE,
        )
    if variant == "dedup-ref-only":
        # Content dedup on References, but Identifiables kept as-is (needs the constraint off).
        return dataclasses.replace(
            AAS_NEO4J_MODEL_CONFIG,
            deduplicated_by_id=set(),
            default_optimization_clauses=_NO_UNIQUE,
        )
    raise SystemExit(f"unknown variant {variant}")


# --- measurement ------------------------------------------------------------------

COUNT_QUERIES = {
    "nodes": "MATCH (n) RETURN count(n) AS v",
    "rels": "MATCH ()-[r]->() RETURN count(r) AS v",
    "node_props": "MATCH (n) RETURN sum(size(keys(n))) AS v",
    "rel_props": "MATCH ()-[r]->() RETURN sum(size(keys(r))) AS v",
    "labels_assigned": "MATCH (n) RETURN sum(size(labels(n))) AS v",
}


def measure(client: AASNeo4JClient) -> dict:
    out = {k: client.execute_clause(q, single=True)["v"] for k, q in COUNT_QUERIES.items()}
    out["by_label"] = {
        r["label"]: r["v"]
        for r in client.execute_clause(
            "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS v ORDER BY v DESC")
    }
    out["by_reltype"] = {
        r["t"]: r["v"]
        for r in client.execute_clause(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS v ORDER BY v DESC")
    }
    return out


def run_variant(variant: str, tier: str, limit: int | None, resolve: bool,
                batch_envs: int = 50) -> dict:
    neo4j_box.fresh()
    client = AASNeo4JClient(neo4j_box.URI, *neo4j_box.AUTH, model_config=config_for(variant))

    rows = tier_rows(tier, limit)
    lstats = LoadStats()
    t0 = time.time()
    nodes: list[dict] = []
    rels: dict = {}
    pending = 0
    upload_time = 0.0

    def flush():
        nonlocal nodes, rels, pending, upload_time
        if not nodes:
            return
        t = time.time()
        client._upload_nodes_and_relationships(nodes, rels, db_batch_size=10000)
        upload_time += time.time() - t
        nodes, rels, pending = [], {}, 0

    for env in iter_environments(rows, lstats):
        n, r = client._process_json_data(env)
        nodes.extend(n)
        client._merge_relationships(rels, r)
        pending += 1
        if pending >= batch_envs:
            flush()
    flush()
    load_seconds = time.time() - t0

    resolve_seconds, ref_edges = 0.0, 0
    if resolve:
        t = time.time()
        ref_edges = client.resolve_references()
        resolve_seconds = time.time() - t

    result = {
        "variant": variant,
        "tier": tier,
        "limit": limit,
        "files": lstats.files,
        "environments": lstats.envs,
        "failed": len(lstats.failed),
        "source_bytes_gz": lstats.raw_bytes,
        "source_bytes_json": lstats.plain_bytes,
        "load_seconds": round(load_seconds, 1),
        "upload_seconds": round(upload_time, 1),
        "resolve_seconds": round(resolve_seconds, 1),
        "reference_edges": ref_edges,
        **measure(client),
    }
    client.driver.close()
    result["disk"] = neo4j_box.store_bytes()
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="t100")
    ap.add_argument("--variant", default="current", help="comma-separated variant names")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resolve", action="store_true", help="also run resolve_references()")
    ap.add_argument("--tag", default="", help="suffix for the result file name")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    for variant in args.variant.split(","):
        res = run_variant(variant, args.tier, args.limit, args.resolve)
        name = f"{args.tier}-{variant}{('-' + args.tag) if args.tag else ''}.json"
        (RESULTS / name).write_text(json.dumps(res, indent=2))
        print(json.dumps({k: v for k, v in res.items()
                          if k not in ("by_label", "by_reltype")}, indent=2))


if __name__ == "__main__":
    main()
