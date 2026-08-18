# Storage optimisation — measured against aas-corpus

How much Neo4j does one AAS cost, and which modelling decisions move that number.
Every figure here comes from `scripts/bench/`, run against the `aas-corpus` tiers
(`t100 ⊂ t1k ⊂ t10k`), one **fresh** Neo4j 5.26 container per measurement — Neo4j never
shrinks a store file, so reusing a database would carry the previous variant's high-water
mark into the next one's disk figure.

```bash
uv run python scripts/bench/storage_bench.py --tier t100 --variant nodedup,current --resolve
uv run python scripts/bench/model_analysis.py --tier t1k     # no database needed
uv run python scripts/bench/prop_profile.py  --tier t100     # what the property store holds
```

Raw results land in `docs/bench/<tier>-<variant>[-<tag>].json`.

---

## 1. What deduplication is worth

Tier t100 — 100 AAS, 48.9 MB gzipped on disk, **24.3 MB of AAS-JSON**, 68 971 elements.

| | nodes | relationships | node props | store | `resolve_references()` |
|---|---|---|---|---|---|
| no deduplication | 145 985 | 2 299 105 | 661 879 | **181.4 MB** | 129.2 s |
| Reference hash + Identifiable id | 73 767 | 140 237 | 303 294 | **37.3 MB** | 0.3 s |

2.0x fewer nodes, 16.4x fewer relationships, 4.9x less disk.

The relationship blow-up is not the raw import — structurally the two graphs differ by
almost nothing (139 829 vs 139 109 edges before resolution). It is `:references`
resolution: without id-dedup the corpus's 5 580 ConceptDescription objects become 5 580
nodes instead of 622, so every semanticId reference resolves to *every* copy of its
target — 2.16 M edges for 45 853 logical references, and 430x slower resolution. Storing
duplicates does not just cost their own bytes; it multiplies everything that points at them.

## 2. Duplicate Identifiables kept their children (fixed)

MERGE-on-id collapsed a repeated Identifiable's *node*, but the duplicate's whole child
subtree was still created and hung off the survivor. On t100 a ConceptDescription carried
**8.2 `embeddedDataSpecifications` on average and up to 78** — waste, and a broken export
(all 78 are emitted). `_drop_duplicate_identifiable_subtrees` now skips a duplicate with
its subtree before anything is written.

| t100 | nodes | rels | node props | store |
|---|---|---|---|---|
| before | 73 767 | 140 237 | 303 294 | 37.31 MB |
| after | **60 252** | **114 776** | **248 901** | **28.83 MB** |
| | −18.3 % | −18.2 % | −17.9 % | −22.7 % |

## 3. `modelType` is a label, not a property

Every element already carries its class chain as Neo4j labels, so the `modelType` property
is one of those labels written a second time on 68 971 nodes. Import drops it; the exporter
restores the most specific AAS class label (`AnnotatedRelationshipElement` is also labelled
`RelationshipElement`).

| t100 | node props | store |
|---|---|---|
| before | 248 901 | 28.83 MB |
| after | **192 833** (−22.5 %) | **27.46 MB** (−4.7 %) |

Cheap: it costs one label lookup on export and nothing at query time — no Cypher in the
project reads the property.

## 4. Where the remaining bytes are

`prop_profile.py` counts what the importer writes, per property, before dedup:

* **227 154 list-valued properties, 198 216 of them single-entry (87 %).** These are the
  flattening of a list-of-dicts (`description` → `description_text` + `description_language`,
  `keys` → `keys_value` + `keys_type`, MLP `value` → `value_text` + `value_language`).
* A one-entry array is **not** a cheap property in Neo4j: it lives in the dynamic array
  store. Measured on an isolated 200 000-node database, two properties per node:

  | encoding | store | per node |
  |---|---|---|
  | two scalars | 20.47 MB | 98 B |
  | two one-entry arrays | 79.34 MB | 393 B |
  | two two-entry arrays | 79.34 MB | 393 B |

  **A single-entry array costs ~4x the same value as a scalar**, and a second entry is free —
  the block is allocated either way.

That makes single-entry list compaction the largest remaining lever, ahead of the shared
metadata node (§5).

## 5. Shared metadata node for Referables — what it would actually buy

The idea: lift the descriptive header of a Referable (`category`, `description`,
`displayName`, `semanticId`) into its own node, deduplicated, with the elements pointing at
it. The repetition is real — on t100, 58 506 elements carry only **2 140 distinct** headers
(21.8x), 8.95 MB of JSON collapsing to 0.41 MB.

But most of those bytes are the `semanticId`, and semanticIds are *already* shared: they are
`Reference` nodes, hash-deduplicated to 1 971 nodes today. Against the current model the win
is only the descriptive part — 25 693 elements carrying 1.86 MB of `category` /
`description` / `displayName`, 866 distinct (12.2x), so ≈1.7 MB of a 27.5 MB store (~6 %)
plus ~55 k property slots. Moving `semanticId` onto the shared node as well would collapse
57 k `semanticId` edges into ~2 k, at the cost of ~58 k new `meta` edges (a wash) and of
every Cypher path that traverses `-[:semanticId]->` — the AASQL compiler, the AASd-107 /
114 / 118 validation queries, the chatbot's semantic tools.

