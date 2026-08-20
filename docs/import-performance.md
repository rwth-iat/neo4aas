# Import performance — measured against aas-corpus

How long it takes to get one AAS into Neo4j, and which parts of the write path that time
belongs to. Companion to [storage-optimisation.md](storage-optimisation.md): that one
measures the bytes a stored AAS costs, this one the seconds.

```bash
uv run python scripts/bench/import_bench.py --tier t1k --pipeline --resolve
uv run python scripts/bench/import_bench.py --tier t1k --cprofile          # Python side
uv run python scripts/bench/import_bench.py --tier t1k --workers 4         # concurrency
```

`import_bench.py` wraps every phase of the write path (source decode, the JSON → node/edge
mapping, deduplication, the Cypher round trips) and reports them separately, so a change is
attributed to a phase rather than to the total. Each run uses a **fresh** Neo4j 5.26
container. Raw results land in `docs/bench/import-<tier>-<tag>.json`.

**Measurement noise.** These numbers come from a laptop Docker VM (10 CPUs, 8 GiB) shared
with other running containers; repeated identical runs vary by ±10 %. Every figure below is
the best of at least two runs, and the ones that matter were re-measured interleaved with
their baseline rather than compared across sessions.

---

## 1. Where the time went

Tier t1k — 1 000 AAS, 542 073 nodes, 1 060 387 relationships.

| phase | seconds | |
|---|---|---|
| decode source (gz / aasx / xml → AAS-JSON) | 10.4 | 11 % |
| `_process_json_data` (JSON → nodes + edges) | ~10 | 10 % |
| **database write** | **82.7** | **84 %** |
| `resolve_references()` | 0.6 | — |
| total | 98.7 | |

The write dominated, and it was three round trips per batch plus a fourth pass over every
node:

1. create/merge the nodes, **returning one Bolt row per node** (elementId + uid),
2. create the relationships, each one looking up both endpoints by elementId and MERGEing,
3. `REMOVE n.uid` over every node just written.

## 2. One query per batch instead of three passes

The three passes existed only because the `uid → node` mapping lived in Python. Keeping it
**server-side** removes all of them: the nodes are created, collected into an
`apoc.map.fromPairs` map keyed by uid, and the relationships are created straight from that
map in the same query. Nothing comes back but the edge count.

That also removes the `uid` property itself. It was written on every node and deleted again
by pass 3 — with the map server-side it never reaches the property bag at all.

Measured on one real 50-environment batch (35 747 nodes, 69 152 edges), fresh database:

| | seconds |
|---|---|
| 3 passes + uid cleanup (before) | 2.85 |
| one fused query | **1.19** |
| — of which nodes | 0.74 |

## 3. Relationships: MERGE was the single most expensive thing

Relationship writes used `apoc.merge.relationship` so that re-importing an edge onto a
reused deduplicated node would not duplicate it. Same batch, only the edge write varying:

| edge write | seconds |
|---|---|
| `apoc.merge.relationship` | 2.97 |
| `apoc.create.relationship` | 1.55 |
| `CREATE (a)-[r:$(type)]->(b)` | **1.19** |

MERGE cost ~4x a CREATE. It is also almost never needed: an edge can only pre-exist if its
**source** node is one the database already held, and the only nodes that come back from
the database are those MERGEd on their content `hash` (a dedup-by-id Identifiable that is
already stored has its whole subtree dropped before the write, so it emits no edges). On
the whole of t100 that is **56 of 139 109 edges — 0.04 %**, all of them `referredSemanticId`.

So the write splits the edges: CREATE for everything leaving a node created in this batch,
MERGE only for edges leaving a hash-merged node. `tests/core/test_dedup.py::
test_edge_out_of_a_reused_reference_is_not_duplicated` pins the MERGE half.

## 4. Overlapping the write with the decode

With the write halved, the source side (decode + mapping, ~20 s of t1k) is no longer noise.
`_overlapped_writes()` hands each finished batch to **one** background writer thread, so
batch N is written while batch N+1 is decoded; both bulk-directory loaders use it.

One writer, never more — see §6.

## 5. Result

| tier | before | fused write | + overlapped decode | speedup |
|---|---|---|---|---|
| t100 (100 AAS) | 17.0 s | 9.1 s | — | 1.9x |
| t1k (1 000 AAS) | 98.7 s | 63.6 s | **51.3 s** | 1.9x |
| t10k (10 000 AAS) | 756.4 s | | **585.9 s** | 1.3x |

The graph is unchanged, entity for entity: t1k is 542 073 nodes / 1 060 387 relationships /
5 683 `:references` edges before and after, t10k 5 377 523 / 10 579 115 / 47 227.

The gain shrinks with scale (1.9x at t1k, 1.3x at t10k) because what the change removes is
per-batch *client* work, while the part that grows with the store — the MERGE-on-hash/id
lookups behind deduplication — becomes random reads once the store outgrows the page cache.
At t10k the t100k tier's headroom, not the round trips, is the next question. The t10k
baseline is the `t10k-current-scalar` storage run (same machine, same batch size, no
overlapped writes).

Cheap Python-side change on the way: relationship deduplication keyed a `sha256` of a
`json.dumps` of every edge. That key is never stored (unlike a node `hash`, which the
database MERGEs on), so it is now a tuple — hashing a million edges cost more than the write
it guarded.

## 6. Concurrent writers: measured and rejected

Several clients writing shards of the same tier concurrently (`--workers N`, t1k):

| workers | seconds | nodes | |
|---|---|---|---|
| 1 (overlapped) | 51.3 | 542 073 | |
| 2 | 46.2 | 544 308 | +0.41 % |
| 4 | 66.7 | 546 259 | +0.77 % |

Two workers buy ~10 % — inside this machine's run-to-run noise — and four are *slower* than
one: the write is lock-bound, not CPU-bound, and concurrent MERGEs on the same hot
Reference / ConceptDescription contend. Worse, the node counts show it is not the same
load: `_drop_duplicate_identifiable_subtrees` asks the database which ids are already
stored, so two workers holding the same Identifiable both see "not stored" and both write
its subtree — the exact defect §2 of the storage document fixed. Concurrency at the write
is the wrong axis; overlapping the *decode* is the one that pays.

## 7. What is left

* **The write is ~80 % of the remaining time** and is roughly linear in entities written
  (~48 k nodes/s, ~69 k edges/s on this machine). The cheapest further win is therefore
  storing fewer entities, which is [storage-optimisation.md](storage-optimisation.md)'s
  subject, not this one's.
* **Offline bulk loading** (`neo4j-admin database import full`) skips the transaction log
  and the property/relationship store bookkeeping entirely and is the standard answer for a
  first load of a large corpus. It needs an offline, empty database and a CSV generator
  that carries out deduplication itself (the importer's in-memory dedup already does this
  for a whole run). Not implemented — it would be a second write path to keep correct, and
  it cannot serve the incremental repository-server case at all.
* **Page cache sizing is operational, not code.** A t10k load with a 1 GiB page cache ran
  ~10x slower: the MERGE-on-hash/id lookups turn into random disk reads once the store
  outgrows the cache.
