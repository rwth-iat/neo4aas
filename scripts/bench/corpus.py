"""Read AAS environments out of an aas-corpus tier.

A tier manifest (``benchmarks/tiers/manifests/<tier>.tsv``) lists one AAS per row with
its path relative to ``instances/``. This module turns those rows into AAS-JSON
environment dicts, whatever the on-disk encoding (json / xml / aasx, each optionally
gzipped) — so a storage experiment sees exactly the same content for every variant.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List

from neo4aas.core.io import aas_suffix, read_bytes
from neo4aas.core.serialization.aasx import AasxToNeo4jImporter
from neo4aas.core.serialization.xml.xml_to_json import xml_to_aas_json

logger = logging.getLogger(__name__)

CORPUS = Path(os.environ.get("AAS_CORPUS", "/Users/igorgarmaev/PycharmProjects/aas-corpus"))
INSTANCES = CORPUS / "instances"
MANIFESTS = CORPUS / "benchmarks" / "tiers" / "manifests"


@dataclass
class TierRow:
    aas_id: str
    path: Path
    vendor: str
    fmt: str


def tier_rows(tier: str, limit: int | None = None) -> List[TierRow]:
    rows: List[TierRow] = []
    with open(MANIFESTS / f"{tier}.tsv", newline="") as fh:
        for rec in csv.DictReader(fh, delimiter="\t"):
            rows.append(TierRow(rec["aas_id"], INSTANCES / rec["path"], rec["vendor"], rec["format"]))
            if limit and len(rows) >= limit:
                break
    return rows


@dataclass
class LoadStats:
    files: int = 0
    envs: int = 0
    failed: List[str] = field(default_factory=list)
    raw_bytes: int = 0        # bytes of the source file as stored (gzipped)
    plain_bytes: int = 0      # bytes of the AAS-JSON environment we actually import


def iter_environments(rows: List[TierRow], stats: LoadStats) -> Iterator[dict]:
    """Yield one AAS-JSON environment dict per manifest row (an .aasx may yield several)."""
    aasx = AasxToNeo4jImporter(xml_importer=None)
    for row in rows:
        stats.files += 1
        try:
            stats.raw_bytes += row.path.stat().st_size
            suffix = aas_suffix(row.path)
            if suffix == ".aasx":
                envs = list(aasx.iter_environments(row.path))
            elif suffix == ".xml":
                envs = [xml_to_aas_json(row.path)]
            else:
                envs = [json.loads(read_bytes(row.path))]
        except Exception as exc:  # corpus is real vendor output; a bad file must not stop a run
            logger.warning("skip %s: %s", row.path, exc)
            stats.failed.append(f"{row.path}: {exc}")
            continue
        for env in envs:
            stats.envs += 1
            stats.plain_bytes += len(json.dumps(env).encode())
            yield env
