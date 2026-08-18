"""Profile the node properties the importer actually writes, without a database.

Neo4j stores every *array* property in its dynamic array store — a one-element
`description_text = ["..."]` costs a whole dynamic record, not a few bytes — so knowing how
many of the flattened list properties are single-entry decides whether a scalar/array
split is worth implementing.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.corpus import LoadStats, iter_environments, tier_rows

from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG, AASNeo4JClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="t100")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    client = AASNeo4JClient(uri=None, user="x", model_config=AAS_NEO4J_MODEL_CONFIG)
    prop_count = Counter()          # prop name -> occurrences
    list_len = Counter()            # prop name -> occurrences with exactly one entry
    kinds = Counter()               # scalar / list-of-N
    str_bytes = Counter()

    ls = LoadStats()
    for env in iter_environments(tier_rows(args.tier, args.limit), ls):
        nodes, _ = client._process_json_data(env)
        for node in nodes:
            for key, value in node.items():
                if key in ("uid", "labels"):
                    continue
                prop_count[key] += 1
                if isinstance(value, list):
                    kinds["list"] += 1
                    kinds[f"list_len_{min(len(value), 5)}"] += 1
                    if len(value) == 1:
                        list_len[key] += 1
                    str_bytes[key] += sum(len(str(v)) for v in value if v is not None)
                else:
                    kinds["scalar"] += 1
                    str_bytes[key] += len(str(value)) if isinstance(value, str) else 0

    print(json.dumps({
        "tier": args.tier,
        "kinds": dict(kinds.most_common()),
        "props": {k: {"n": v, "single_entry_lists": list_len.get(k, 0), "str_bytes": str_bytes[k]}
                  for k, v in prop_count.most_common(25)},
    }, indent=2))


if __name__ == "__main__":
    main()
