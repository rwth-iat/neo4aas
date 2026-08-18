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

## 6. Single-entry flattened lists as scalars

87 % of the flattened list properties hold one entry, and §4 measured a one-entry array at
~4x the cost of a scalar. Import now writes a scalar for the configured properties
(`Referable` description/displayName, `MultiLanguageProperty` value,
`DataSpecificationIec61360` preferredName/shortName/definition); `Reference.keys` keeps its
list encoding because Cypher indexes it. The exporter accepts both encodings, and Cypher
that indexes such a property goes through `apoc.convert.toList`
(`core.utils.cypher_as_list`), so a store written before the change still reads correctly.

| t100 | node props | store |
|---|---|---|
| before | 192 833 | 27.46 MB |
| after | 192 833 | **23.10 MB** (−15.9 %) |

Same number of properties, 4.4 MB less disk — the encoding is the whole difference.

## 7. Where the bytes are now

t100, after §2 + §3 + §6, 23.10 MB total:

| store file | size | what it holds |
|---|---|---|
| property store | 7.82 MB | the 192 833 node + 60 106 relationship properties |
| relationship store | 3.92 MB | 114 776 edges, ~34 B each |
| array store | 3.09 MB | what is left of the parallel lists (2-entry en/de, Reference keys) |
| string store | 2.18 MB | the long strings (descriptions, ids) |
| node store | 0.91 MB | 60 252 nodes, ~15 B each |
| schema (indexes) | 4.37 MB | id constraint, idShort, Reference.target_id(_base), hash |

Cumulative on t100: **37.31 MB → 23.10 MB (−38.1 %)**, and 7.9x smaller than the same
corpus stored without deduplication.

## 8. Scaling: t100 vs t1k

Same code, ten times the corpus (1 000 AAS, 236.5 MB of AAS-JSON, 668 274 elements):

| tier | nodes | rels | props | store | load |
|---|---|---|---|---|---|
| t100, all three changes | 60 252 | 114 776 | 252 939 | 23.10 MB | 17.1 s |
| t1k, all three changes | 542 073 | 1 060 387 | 2 260 959 | **251.76 MB** | 87.1 s |
| t1k, subtree fix only | 542 073 | 1 060 387 | 2 780 815 | 293.55 MB | 85.0 s |

The graph is linear in corpus size (9.0x the nodes, 9.2x the edges, 10.9x the store for 10x
the AAS), so the per-AAS cost holds: **≈252 KB of Neo4j per AAS, from ≈237 KB of AAS-JSON**
— a store slightly larger than the JSON it came from, index included. Load throughput is
also flat: 11.5 AAS/s at t1k against 5.8 at t100 (t100 is dominated by the larger
per-file ABB/Bürkert packages).

Property-count reduction holds at scale (−23.4 % at t1k, −22.5 % at t100); the disk
reduction is smaller at t1k (−14.2 %) because the array-heavy DataSpecification content is
a smaller share of a bigger, more varied corpus.

## 9. Ideas measured and *not* taken

* **Shared metadata node** (§5) — ~6 % of the store today, and it moves `description` /
  `displayName` / `category` behind a hop that the AASQL compiler, the constraint checker
  and the chatbot would all have to learn. Worth revisiting if descriptions grow.
* **Inlining single-key `ExternalReference`s** as properties on the element: 98 % of all
  references are single-key external, so this removes ~57 k `semanticId` edges (~1.9 MB of
  relationship store) — but it adds ~114 k properties (~2.3 MB), loses the `:references`
  edge to the ConceptDescription, and breaks every semanticId query. Net negative.
* **Whole-subtree sharing** (identical element subtrees stored once): the redundancy is
  real and grows with the corpus (4.1x at t100, 6.8x at t1k), but sharing an element node
  between two AAS makes "which submodel does this element belong to" ambiguous and any
  update to one instance change the other. Only defensible for a read-only catalogue.
