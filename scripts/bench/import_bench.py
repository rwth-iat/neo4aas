"""Measure where the time goes when a corpus tier is imported into Neo4j.

Storage bench answers "how many bytes"; this one answers "how many seconds, spent where".
Every phase of the write path is timed separately — source decode, the Python object →
node/relationship mapping, in-memory deduplication, and each Cypher round trip — so a
change can be attributed to a phase instead of to the total.

    uv run python scripts/bench/import_bench.py --tier t100
    uv run python scripts/bench/import_bench.py --tier t1k --tag baseline --cprofile
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import logging
import pstats
import sys
import threading
from contextlib import ExitStack
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench import neo4j_box
from bench.corpus import LoadStats, iter_environments, tier_rows

from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG, AASNeo4JClient

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "bench"

# Phases of one upload, in call order. Each is a method on the client that we wrap.
PHASES = [
    "_process_json_data",
    "_drop_duplicate_identifiable_subtrees",
    "_group_nodes_by_label",
    "_deduplicate_nodes",
    "_deduplicate_rels",
    "_write_buckets",
    "_write_graph",
]


class Timings:
    def __init__(self) -> None:
        self.seconds: Counter = Counter()
        self.calls: Counter = Counter()
        self.queries = 0

    def wrap(self, obj, name: str) -> None:
        original = getattr(obj, name)

        def timed(*args, **kwargs):
            t = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.seconds[name] += time.perf_counter() - t
                self.calls[name] += 1

        setattr(obj, name, timed)

    def count_queries(self, driver) -> None:
        """Count Cypher round trips by wrapping Session.run on every session opened."""
        original_session = driver.session

        def session(*args, **kwargs):
            s = original_session(*args, **kwargs)
            run = s.run

            def counted(*a, **kw):
                self.queries += 1
                return run(*a, **kw)

            s.run = counted
            return s

        driver.session = session


def run_parallel(tier: str, limit: int | None, batch_envs: int, workers: int,
                 resolve: bool) -> dict:
    """Load a tier with `workers` independent clients writing concurrently.

    Data-parallel, not pipelined: each worker decodes and writes its own shard of the
    manifest with its own importer state. It is the upper bound on what concurrency buys —
    and it is *not* equivalent to the serial load: `_drop_duplicate_identifiable_subtrees`
    asks the database which ids are already stored, so two workers holding the same
    Identifiable can both see "not stored" and both write its subtree. The measured node
    count says how much duplication that actually produced.
    """
    neo4j_box.fresh()
    schema = AASNeo4JClient(neo4j_box.URI, *neo4j_box.AUTH, model_config=AAS_NEO4J_MODEL_CONFIG)
    rows = tier_rows(tier, limit)
    shards = [rows[i::workers] for i in range(workers)]
    stats = [LoadStats() for _ in shards]

    def load(shard, lstats):
        client = AASNeo4JClient(neo4j_box.URI, *neo4j_box.AUTH,
                                model_config=AAS_NEO4J_MODEL_CONFIG, auto_optimize=False)
        nodes, rels, pending = [], {}, 0
        for env in iter_environments(shard, lstats):
            n, r = client._process_json_data(env)
            nodes.extend(n)
            client._merge_relationships(rels, r)
            pending += 1
            if pending >= batch_envs:
                client._upload_nodes_and_relationships(nodes, rels)
                nodes, rels, pending = [], {}, 0
        if nodes:
            client._upload_nodes_and_relationships(nodes, rels)
        client.driver.close()

    t0 = time.perf_counter()
    threads = [threading.Thread(target=load, args=(sh, st)) for sh, st in zip(shards, stats)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    load_seconds = time.perf_counter() - t0

    resolve_seconds, ref_edges = 0.0, 0
    if resolve:
        t = time.perf_counter()
        ref_edges = schema.resolve_references()
        resolve_seconds = time.perf_counter() - t

    envs = sum(s.envs for s in stats)
    result = {
        "tier": tier, "workers": workers, "batch_envs": batch_envs, "environments": envs,
        "load_seconds": round(load_seconds, 1),
        "aas_per_second": round(envs / load_seconds, 2),
        "resolve_seconds": round(resolve_seconds, 1), "reference_edges": ref_edges,
        "nodes": schema.execute_clause("MATCH (n) RETURN count(n) AS v", single=True)["v"],
        "rels": schema.execute_clause("MATCH ()-[r]->() RETURN count(r) AS v", single=True)["v"],
    }
    schema.driver.close()
    return result


def run(tier: str, limit: int | None, batch_envs: int, db_batch_size: int,
        resolve: bool, profile: bool, pipeline: bool = False) -> dict:
    neo4j_box.fresh()
    client = AASNeo4JClient(neo4j_box.URI, *neo4j_box.AUTH,
                            model_config=AAS_NEO4J_MODEL_CONFIG)

    timings = Timings()
    for phase in PHASES:
        if hasattr(client, phase):
            timings.wrap(client, phase)
    timings.count_queries(client.driver)

    rows = tier_rows(tier, limit)
    lstats = LoadStats()
    nodes: list[dict] = []
    rels: dict = {}
    pending = 0
    decode_seconds = 0.0
    upload_seconds = 0.0

    def flush():
        nonlocal nodes, rels, pending, upload_seconds
        if not nodes:
            return
        t = time.perf_counter()
        client._upload_nodes_and_relationships(nodes, rels, db_batch_size=db_batch_size)
        upload_seconds += time.perf_counter() - t
        nodes, rels, pending = [], {}, 0

    # --pipeline: use the importer's own overlapped-write helper, so what is measured is
    # the shipped bulk-loader behaviour (one writer thread, batch N written while batch
    # N+1 is decoded), not a bench-only imitation of it.
    writes = ExitStack()
    if pipeline:
        submit = writes.enter_context(client._overlapped_writes())

        def flush():  # noqa: F811 - pipelined replacement
            nonlocal nodes, rels, pending, upload_seconds
            if not nodes:
                return
            t = time.perf_counter()
            submit(nodes, rels, None, db_batch_size)
            upload_seconds += time.perf_counter() - t
            nodes, rels, pending = [], {}, 0

    profiler = cProfile.Profile() if profile else None
    t0 = time.perf_counter()
    if profiler:
        profiler.enable()

    envs = iter_environments(rows, lstats)
    while True:
        t = time.perf_counter()
        env = next(envs, None)
        decode_seconds += time.perf_counter() - t
        if env is None:
            break
        n, r = client._process_json_data(env)
        nodes.extend(n)
        client._merge_relationships(rels, r)
        pending += 1
        if pending >= batch_envs:
            flush()
    flush()
    writes.close()

    if profiler:
        profiler.disable()
    load_seconds = time.perf_counter() - t0

    resolve_seconds, ref_edges = 0.0, 0
    if resolve:
        t = time.perf_counter()
        ref_edges = client.resolve_references()
        resolve_seconds = time.perf_counter() - t

    counts = {
        "nodes": client.execute_clause("MATCH (n) RETURN count(n) AS v", single=True)["v"],
        "rels": client.execute_clause("MATCH ()-[r]->() RETURN count(r) AS v", single=True)["v"],
    }
    result = {
        "tier": tier,
        "limit": limit,
        "batch_envs": batch_envs,
        "pipeline": pipeline,
        "db_batch_size": db_batch_size,
        "environments": lstats.envs,
        "source_bytes_json": lstats.plain_bytes,
        "load_seconds": round(load_seconds, 1),
        "aas_per_second": round(lstats.envs / load_seconds, 2) if load_seconds else None,
        "decode_seconds": round(decode_seconds, 1),
        "upload_seconds": round(upload_seconds, 1),
        "resolve_seconds": round(resolve_seconds, 1),
        "reference_edges": ref_edges,
        "cypher_queries": timings.queries,
        "phase_seconds": {k: round(v, 1) for k, v in timings.seconds.most_common()},
        "phase_calls": dict(timings.calls),
        **counts,
    }
    if profiler:
        buf = io.StringIO()
        pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(35)
        result["cprofile"] = buf.getvalue()
    client.driver.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="t100")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch-envs", type=int, default=50)
    ap.add_argument("--db-batch-size", type=int, default=10000)
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--workers", type=int, default=1,
                    help="load with N concurrent clients (data-parallel upper bound)")
    ap.add_argument("--pipeline", action="store_true",
                    help="overlap decode/mapping with the database write")
    ap.add_argument("--cprofile", action="store_true", help="also collect a Python profile")
    ap.add_argument("--tag", default="baseline")
    args = ap.parse_args()

    if args.workers > 1:
        res = run_parallel(args.tier, args.limit, args.batch_envs, args.workers, args.resolve)
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"import-{args.tier}-{args.tag}.json").write_text(json.dumps(res, indent=2))
        print(json.dumps(res, indent=2))
        return

    res = run(args.tier, args.limit, args.batch_envs, args.db_batch_size,
              args.resolve, args.cprofile, args.pipeline)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"import-{args.tier}-{args.tag}.json").write_text(json.dumps(res, indent=2))
    printable = {k: v for k, v in res.items() if k != "cprofile"}
    print(json.dumps(printable, indent=2))
    if "cprofile" in res:
        print(res["cprofile"][:4000])


if __name__ == "__main__":
    main()
