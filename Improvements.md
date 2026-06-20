# aas4graph — Mapping Optimization Ideas

Ideas for optimizing the AAS → Neo4j mapping. Grounded in the current schema.

**Current indexes** (only these): `Identifiable.id`, `Referable.idShort`, `:value(list_index)`
— see [aas_neo4j_client.py:48-50](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py#L48-L50).

| # | Idea | Impact | Effort | Risk | Status |
|---|------|--------|--------|------|--------|
| 1 | `Identifiable.id` → uniqueness constraint | correctness + perf | low | low | ✅ done |
| 2 | DB-level dedup (MERGE on `hash`) + index | storage + correctness | med | med | ✅ done |
| 3 | Denormalize `target_id = keys_value[0]` (indexed) | query perf | low | low | ✅ done (in #7) |
| 4 | Remove `uid` leak | correctness + storage | low | low | ✅ done |
| 5 | Remove redundant `:child` edge (keep semantic) | storage + simpler model | med | med | ✅ done |
| 6 | Typed value shadow prop for range queries | query perf | med | low |
| 7 | Incremental `resolve_references()` (+ `target_id` index) | write perf | med | low | ✅ done |
| 8 | Fix dedup self-loop (`referredSemanticId`) | correctness | med | low |

---

## High-impact, low-effort

### 1. `Identifiable.id` → uniqueness CONSTRAINT (not just INDEX)
Today it's a plain INDEX ([aas_neo4j_client.py:48](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py#L48)) and `identifiable_exists()` does a manual pre-check before insert — racy and an extra round-trip. A uniqueness constraint provides the index **and** enforces no duplicate ids at the DB level.

```cypher
CREATE CONSTRAINT identifiable_id IF NOT EXISTS
FOR (n:Identifiable) REQUIRE n.id IS UNIQUE;
```

**✅ Implemented** — replaced the `Identifiable.id` index in `default_optimization_clauses` ([aas_neo4j_client.py:47-53](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py#L47-L53)). Tests in `test_schema_constraints.py` (constraint present, duplicate id rejected at DB level, idempotent re-run). The manual `identifiable_exists()` pre-check is kept for a clean `KeyError`; the constraint is the backstop.

### 2. Index / constrain the dedup key
`Reference` and `ConceptDescription` are deduplicated by SHA256 `hash`, but **no index exists on `hash`** — every dedup MERGE scans. Add an index, or a uniqueness constraint so dedup happens DB-side via `MERGE` and the in-memory hash dict can be dropped.

```cypher
CREATE INDEX reference_hash IF NOT EXISTS FOR (r:Reference) ON (r.hash);
CREATE INDEX concept_description_hash IF NOT EXISTS FOR (c:ConceptDescription) ON (c.hash);
```

**✅ Implemented** — node creation now MERGEs deduplicated-type nodes on `hash` (`apoc.merge.node`) instead of plain CREATE, and relationship creation uses `apoc.merge.relationship`, so a Reference/ConceptDescription imported by a *different* client/process reuses the canonical node and accrues no duplicate edges ([neo4j_import.py `_create_nodes` / `_create_relationships`](aas_mapping/aas_neo4j_adapter/jsonification/neo4j_import.py)). A backing `hash` index is created for every `deduplicated_object_types` entry (derived in `optimize_database()`), so adding/removing a deduplicated type needs no index edits and never silently falls back to a full scan. Tests in `test_dedup.py` (cross-client Reference dedup with shared incoming edges; distinct refs not merged). Plain index (not uniqueness constraint) chosen: MERGE itself guarantees a single node, and over-constraining interacts with the #8 self-loop case.

### 3. Denormalize the resolution / lookup key → ⤳ merged into #7
`keys_value[0]` is a **list element** → Neo4j cannot index it. The original idea was to store a scalar `target_id = keys_value[0]` on each Reference and index it for an indexed join.

**Reassessed — no standalone benefit, merged into #7.** After the resolution work, no current path needs it:
- the `$aas`+`$sme` join traverses the materialized `:references` edge, not `keys_value[0]`;
- `resolve_references()` looks up `Identifiable {id: $k0}`, which already hits the `Identifiable.id` constraint index (#1);
- resolution scans all ModelReferences anyway, so `target_id` saves nothing there.

It also costs duplicated data (must stay in sync with `keys_value`) and an extra internal prop to strip on export. The **only** payoff is enabling **incremental** resolution (#7) — finding "refs whose target = this id" by an indexed lookup *from* the Reference side. So `target_id` + its index are folded into #7.

### 4. Kill the `uid` leak
Every node carries a never-cleaned `uid` int that leaks into exported dicts (cleanup step is commented out — tracked in TODOs.md "Open Bugs"). Remove it post-import. Storage + round-trip cleanliness.

- File: [jsonification/neo4j_import.py](aas_mapping/aas_neo4j_adapter/jsonification/neo4j_import.py)

**✅ Implemented** — `_upload_nodes_and_relationships` now runs `_cleanup_uids_in_session` for the current batch's nodes after relationship wiring (kept the in-memory `uid → elementId` map; `hash` preserved for dedup). Removed the now-pointless per-label `uid` index. Exposed and fixed a latent exporter bug: a node with no scalar properties (e.g. `EmbeddedDataSpecification`) lost its only property (`uid`) and the subgraph JSON then omits `properties`; `_get_node_properties` now tolerates a missing `properties` key. Tests in `test_uid_cleanup.py` (no `uid` on any node post-import; `hash` retained).

---

## Structural (more effort, bigger payoff)

### 5. Remove the redundant `:child` edge

**Reassessed** — the original premise ("doubles all relationships") was wrong: `:child` was created **only for dict-valued Referable children**, not list members, so it was inconsistent partial redundancy. Better than collapsing *into* `:child` is to **remove it** and keep only the semantic edges — the AASQL compiler already traverses by semantic name, so it needs no change.

**✅ Implemented.** Dropped `:child` creation (importer + `add_submodel_element`). Rewrote `_find_node_clause` to traverse semantic containment edges generically, matching each hop by `idShort` (Collection/Submodel member) or `e.list_index` (SubmodelElementList member). Only `_find_node_clause` depended on `:child`; the exporter already stripped it (`virtual_relationships`) and validation already avoided it.

Exposed and fixed two latent bugs in the (previously untested) navigation path:
- `_find_node` returned the legacy `ID()` as if it were a list (`len()`/`[0]`) and used `ID` while the importer wires relationships by `elementId` — now returns the node's `elementId` as a scalar (with a multiple-match guard);
- `remove_referable` never deleted the **target root** (its container edge counted as "external incoming"), so removals deleted nothing — now the root is always deleted while genuinely-shared nodes (deduped References/CDs) are kept.

Tests in `test_node_navigation.py` (no `:child` edges; find/add/remove under a Collection; find under a List by index; `get_referable` with a path). Bonus: list-path navigation, previously broken (`:child` never existed for list members and the index was matched as an idShort), now works.

### 6. Typed value shadow for range queries
SME `value` is stored as a string; `$gt`/`$lt` do `toFloat()` at query time, so no index can apply. Store a typed shadow property (`value_num` / `value_dt`) derived from `valueType` and index it → fast numeric/temporal filters. Round-trip still uses the raw string.

### 7. Incremental reference resolution (includes #3 `target_id`)
`resolve_references()` drops and rebuilds **all** `:references` edges on every object-store write — O(all refs) per `add`. Scope it to:
- references contained in the just-added/removed subgraph, and
- dangling references whose target is the new/removed id.

The second case needs an **indexed lookup from the Reference side** by target id — which is exactly the denormalized `target_id = keys_value[0]` from #3. So fold #3 in here:
- write `target_id` when creating a Reference (and keep it in sync if `keys_value` changes);
- index `:Reference(target_id)`;
- add `target_id` to `NEO4J_INTERNAL_NODE_KEYS` so it is stripped on export;
- incremental resolve: `MATCH (r:Reference {target_id: $new_id})` (indexed) instead of scanning all.

- File: [aas_neo4j_client.py](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py) `resolve_references()`

**✅ Implemented.** Added `resolve_references_for(identifier)`: resolves only references **inside** that Identifiable's subgraph plus references **targeting** it (`target_id == id`, indexed) — the two sets affected when an Identifiable appears. `Neo4jObjectStore.add`/`commit` call it instead of the full rebuild; `discard`/`remove` drop the resolve call entirely, since `DETACH DELETE` already removes every `:references` edge into the deleted subtree. The full `resolve_references()` remains for bulk imports. `#3` folded in: `target_id = keys_value[0]` is written on every Reference at import (References are content-addressed/immutable, so it never needs re-sync), indexed via `:Reference(target_id)`, and stripped on export through `NEO4J_INTERNAL_NODE_KEYS`. Per-write cost drops from O(all refs) to O(refs in the object + refs targeting it). Tests in `test_incremental_resolution.py`.

### 8. Fix the dedup self-loop
`referredSemanticId` is stored as a relationship, invisible to the SHA256 hash, so two References differing only in `referredSemanticId` collapse into one node → self-loops, breaking round-trip (`IDTA 02056` xfail). Fold the `referredSemanticId` target into the hash, or exclude such References from dedup.

- Files: [jsonification/neo4j_import.py](aas_mapping/aas_neo4j_adapter/jsonification/neo4j_import.py), [aas_neo4j_client.py](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py)

---

## Suggested order
**1 + 2 + 3 + 4** first — additive index/schema + cleanup, no migration, immediate import + query speedup, low risk. Then **8** (correctness), **7** (write perf), **6** (range queries). **5** last — biggest storage win but touches the compiler.
