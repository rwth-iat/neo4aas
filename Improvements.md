# aas4graph — Mapping Optimization Ideas

Ideas for optimizing the AAS → Neo4j mapping. Grounded in the current schema.

**Current indexes** (only these): `Identifiable.id`, `Referable.idShort`, `:value(list_index)`
— see [aas_neo4j_client.py:48-50](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py#L48-L50).

| # | Idea | Impact | Effort | Risk | Status |
|---|------|--------|--------|------|--------|
| 1 | `Identifiable.id` → uniqueness constraint | correctness + perf | low | low | ✅ done |
| 2 | DB-level dedup (MERGE on `hash`) + index | storage + correctness | med | med | ✅ done |
| 3 | Denormalize `target_id = keys_value[0]` (indexed) | query perf | low | low |
| 4 | Remove `uid` leak | correctness + storage | low | low | ✅ done |
| 5 | Collapse parallel `:child` + semantic edge | storage | high | med |
| 6 | Typed value shadow prop for range queries | query perf | med | low |
| 7 | Incremental `resolve_references()` | write perf | med | low |
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

### 3. Denormalize the resolution / lookup key
`keys_value[0]` is a **list element** → Neo4j cannot index it. Reference resolution and the AAS→SM join repeatedly match `Identifiable.id = r.keys_value[0]` (list deref, unindexed on the Reference side). Store a scalar `target_id = keys_value[0]` on each Reference and index it → indexed join.

- Affects: `resolve_references()` and the `$aas`+`$sme` bridge in [ast_to_cypher.py](aas_mapping/aas_neo4j_adapter/querification/ast_to_cypher.py).

### 4. Kill the `uid` leak
Every node carries a never-cleaned `uid` int that leaks into exported dicts (cleanup step is commented out — tracked in TODOs.md "Open Bugs"). Remove it post-import. Storage + round-trip cleanliness.

- File: [jsonification/neo4j_import.py](aas_mapping/aas_neo4j_adapter/jsonification/neo4j_import.py)

**✅ Implemented** — `_upload_nodes_and_relationships` now runs `_cleanup_uids_in_session` for the current batch's nodes after relationship wiring (kept the in-memory `uid → elementId` map; `hash` preserved for dedup). Removed the now-pointless per-label `uid` index. Exposed and fixed a latent exporter bug: a node with no scalar properties (e.g. `EmbeddedDataSpecification`) lost its only property (`uid`) and the subgraph JSON then omits `properties`; `_get_node_properties` now tolerates a missing `properties` key. Tests in `test_uid_cleanup.py` (no `uid` on any node post-import; `hash` retained).

---

## Structural (more effort, bigger payoff)

### 5. Collapse the parallel `:child` + semantic edge
Every Referable child currently gets **two** edges — `:child` *and* the semantic one (`:value` / `:submodelElements`):
[aas_neo4j_client.py:166-167](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py#L166-L167),
[neo4j_import.py:292-294](aas_mapping/aas_neo4j_adapter/jsonification/neo4j_import.py#L292-L294).

This doubles relationship storage and forced the resolver to dedup double matches. Option: keep a **single** edge (`:child`) carrying `role` + `list_index` as properties → roughly half the relationships, uniform traversal.

- Trade-off: the AASQL compiler traverses by semantic name (`-[:value]->`, `-[:submodelElements]->`); it would need to filter on a `role` property instead. Bigger blast radius.

### 6. Typed value shadow for range queries
SME `value` is stored as a string; `$gt`/`$lt` do `toFloat()` at query time, so no index can apply. Store a typed shadow property (`value_num` / `value_dt`) derived from `valueType` and index it → fast numeric/temporal filters. Round-trip still uses the raw string.

### 7. Incremental reference resolution
`resolve_references()` drops and rebuilds **all** `:references` edges on every object-store write — O(all refs) per `add`. Scope it to the touched subgraph plus dangling refs whose `target_id` equals the new id. (Flagged in TODOs.md.)

- File: [aas_neo4j_client.py](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py) `resolve_references()`

### 8. Fix the dedup self-loop
`referredSemanticId` is stored as a relationship, invisible to the SHA256 hash, so two References differing only in `referredSemanticId` collapse into one node → self-loops, breaking round-trip (`IDTA 02056` xfail). Fold the `referredSemanticId` target into the hash, or exclude such References from dedup.

- Files: [jsonification/neo4j_import.py](aas_mapping/aas_neo4j_adapter/jsonification/neo4j_import.py), [aas_neo4j_client.py](aas_mapping/aas_neo4j_adapter/aas_neo4j_client.py)

---

## Suggested order
**1 + 2 + 3 + 4** first — additive index/schema + cleanup, no migration, immediate import + query speedup, low risk. Then **8** (correctness), **7** (write perf), **6** (range queries). **5** last — biggest storage win but touches the compiler.
