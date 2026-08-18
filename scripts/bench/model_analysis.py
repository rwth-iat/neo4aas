"""Offline (no database) estimate of what each storage model would save.

Walks the same AAS environments the loader would import and counts, per design idea,
how many nodes / relationships / property slots it removes. Cheap enough to run over a
whole tier, so an experiment is only implemented once the numbers justify it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.corpus import LoadStats, iter_environments, tier_rows

META_KEYS = ("category", "description", "displayName", "semanticId", "idShort")
# The same header without idShort: idShort has to stay on the element itself (it is the
# navigation key of an idShort path), so this is the variant that is actually implementable.
META_KEYS_NO_IDSHORT = tuple(k for k in META_KEYS if k != "idShort")
# Descriptive header only: leaves the `semanticId` edge where it is, so the validation
# queries, the AASQL compiler and the chatbot tools that traverse `-[:semanticId]->` keep
# working unchanged. The cheap-to-adopt variant of the same idea.
META_KEYS_DESC_ONLY = ("category", "description", "displayName")


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


class Stats:
    def __init__(self) -> None:
        self.c = Counter()
        self.sets: dict[str, set] = defaultdict(set)
        # hash -> byte size, so "how much would sharing this save" is answerable in bytes
        self.bytes_of: dict[str, dict[str, int]] = defaultdict(dict)

    def add(self, key: str, n: int = 1) -> None:
        self.c[key] += n

    def uniq(self, key: str, value) -> None:
        self.sets[key].add(value)


def walk(obj, st: Stats, parent_kind: str = "") -> None:
    if isinstance(obj, list):
        for item in obj:
            walk(item, st, parent_kind)
        return
    if not isinstance(obj, dict):
        return

    model_type = obj.get("modelType")
    if model_type:
        st.add("elements")
        st.add(f"element:{model_type}")
        # (A) meta tuple of a Referable: how often is the identical descriptive header repeated?
        meta = {k: obj.get(k) for k in META_KEYS if obj.get(k) is not None}
        if meta:
            st.add("meta_bearing")
            st.uniq("meta", h(meta))
            st.add("meta_props", len(meta))
            st.add("meta_bytes", len(json.dumps(meta, sort_keys=True)))
            st.bytes_of["meta"][h(meta)] = len(json.dumps(meta, sort_keys=True))
        meta_desc = {k: obj.get(k) for k in META_KEYS_DESC_ONLY if obj.get(k) is not None}
        if meta_desc:
            st.add("meta_desc_bearing")
            st.uniq("meta_desc", h(meta_desc))
            st.add("meta_desc_bytes", len(json.dumps(meta_desc, sort_keys=True)))
            st.bytes_of["meta_desc"][h(meta_desc)] = len(json.dumps(meta_desc, sort_keys=True))
        meta_ni = {k: obj.get(k) for k in META_KEYS_NO_IDSHORT if obj.get(k) is not None}
        if meta_ni:
            st.add("meta_ni_bearing")
            st.uniq("meta_ni", h(meta_ni))
            st.add("meta_ni_bytes", len(json.dumps(meta_ni, sort_keys=True)))
            st.bytes_of["meta_ni"][h(meta_ni)] = len(json.dumps(meta_ni, sort_keys=True))
        # (E) whole-subtree identity: identical element subtrees across the corpus
        st.uniq("subtree", h(obj))
        st.add("subtree_total")
        st.add("subtree_bytes", len(json.dumps(obj, sort_keys=True)))
        st.bytes_of["subtree"][h(obj)] = len(json.dumps(obj, sort_keys=True))

    if obj.get("type") in ("ExternalReference", "ModelReference"):
        st.add("references")
        st.uniq("reference", h(obj))
        keys = obj.get("keys") or []
        if len(keys) == 1 and obj["type"] == "ExternalReference" and "referredSemanticId" not in obj:
            st.add("ref_single_key_external")
    if "dataSpecification" in obj and "dataSpecificationContent" in obj:
        st.add("eds")
        st.uniq("eds", h(obj))
    if parent_kind == "administration":
        st.add("administration")

    for key, value in obj.items():
        walk(value, st, key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="t100")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    st = Stats()
    ls = LoadStats()
    envs = 0
    for env in iter_environments(tier_rows(args.tier, args.limit), ls):
        envs += 1
        for key in ("assetAdministrationShells", "submodels", "conceptDescriptions"):
            for obj in env.get(key, []):
                st.add(f"identifiable:{key}")
                st.uniq("identifiable_id", obj.get("id"))
                walk(obj, st)

    out = {
        "tier": args.tier,
        "environments": envs,
        "counts": dict(st.c.most_common()),
        "distinct": {k: len(v) for k, v in st.sets.items()},
        # bytes the corpus spends on each shape vs. what one shared copy would cost
        "distinct_bytes": {k: sum(v.values()) for k, v in st.bytes_of.items()},
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
