"""Throwaway Neo4j container for storage measurements.

Every variant gets a *fresh volume*: Neo4j never shrinks its store files, so a
`DETACH DELETE` reset would carry the previous variant's high-water mark into the next
one's disk figure. Store size is read after a restart, because Community has no
`db.checkpoint()` — a clean shutdown is the only way to force the store to disk.
"""
from __future__ import annotations

import subprocess
import time

NAME = "neo4aas-bench"
VOLUME = "neo4aas_bench_data"
URI = "bolt://localhost:7691"
AUTH = ("neo4j", "12345678")


def _run(*args: str, check: bool = True) -> str:
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def fresh(heap: str = "4G", pagecache: str = "2G") -> None:
    _run("docker", "rm", "-f", NAME, check=False)
    _run("docker", "volume", "rm", VOLUME, check=False)
    _run(
        "docker", "run", "-d", "--name", NAME,
        "-p", "7691:7687", "-p", "7475:7474",
        "-e", "NEO4J_AUTH=neo4j/12345678",
        "-e", 'NEO4J_PLUGINS=["apoc"]',
        "-e", "NEO4J_dbms_security_procedures_unrestricted=apoc.*",
        "-e", f"NEO4J_server_memory_heap_initial__size={heap}",
        "-e", f"NEO4J_server_memory_heap_max__size={heap}",
        "-e", f"NEO4J_server_memory_pagecache_size={pagecache}",
        "-v", f"{VOLUME}:/data",
        "neo4j:5",
    )
    wait_up()


def wait_up(timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = subprocess.run(["docker", "exec", NAME, "wget", "-qO", "/dev/null", "http://localhost:7474"],
                           capture_output=True)
        if p.returncode == 0:
            time.sleep(2)
            return
        time.sleep(3)
    raise TimeoutError("neo4j did not come up")


def store_bytes() -> dict[str, int]:
    """Restart (forces a checkpoint on shutdown) and report on-disk store sizes."""
    _run("docker", "restart", NAME)
    wait_up()
    out = _run("docker", "exec", NAME, "du", "-sb", "/data/databases/neo4j", "/data/transactions/neo4j")
    sizes = {}
    for line in out.splitlines():
        n, path = line.split("\t")
        sizes["store" if "databases" in path else "txlogs"] = int(n)
    return sizes
