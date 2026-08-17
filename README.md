# neo4aas

A library for mapping [Asset Administration Shell (AAS)](https://industrialdigitaltwin.org/content-hub/aasspecifications) data to a [Neo4j](https://neo4j.com/) graph database, including bidirectional serialization and a query language compiler (AASQL → Cypher).

---

## Architecture

The distribution is one library plus three apps. `neo4aas.core` depends on the Neo4j
driver and nothing else; everything that touches the basyx SDK is confined to
`basyx_ext/` and `eclass/`, so `neo4aas[mcp]` and `neo4aas[chatbot]` install without
it. `tests/test_layering.py` and the `core-without-basyx` CI job enforce this.

```
src/neo4aas/
├── core/                        # THE LIBRARY — neo4j driver only
│   ├── client.py                # Main API: AASNeo4JClient
│   ├── base.py                  # BaseNeo4JClient, Neo4jModelConfig
│   ├── utils.py                 # UploadStats, hash/IRDI helpers
│   ├── fixers.py                # Import-time repair of non-conformant AAS data
│   ├── validation.py            # AASConstraintChecker — spec constraint validation
│   ├── abstract.py              # Template-submodel merge logic
│   ├── serialization/           # every AAS format <-> Neo4j path
│   │   ├── json/{importer,exporter}.py
│   │   ├── xml/{importer,xml_to_json}.py
│   │   └── aasx.py              # AASX (zip) ingestion, wraps the XML importer
│   └── query/                   # AASQL -> Cypher
│       ├── aasql_to_ast.py      # AASQL JSON → AST (parser)
│       ├── ast_nodes.py         # AST node type definitions
│       ├── ast_to_cypher.py     # AST → Cypher (compiler)
│       └── aasql_to_cypher.py   # Entry point: convert_aasql_to_cypher()
├── agent_tools.py               # Read-only LLM-facing tools over core (mcp + chatbot)
├── basyx_ext/                   # basyx-python-sdk integration  [extra: basyx]
│   ├── object_store.py          # Neo4jObjectStore (AbstractObjectStore)
│   └── server/                  # AAS Repository server        [extra: server]
├── eclass/                      # ECLASS dictionary -> ConceptDescriptions [extra: eclass]
├── mcp/                         # MCP server app               [extra: mcp]
└── chatbot/                     # LangGraph chatbot app        [extra: chatbot]

tests/                           # mirrors src/neo4aas/; fixtures in tests/data/
examples/                        # AASQL queries, expected ASTs/Cypher, IDTA submodels
deploy/                          # docker/ images; demonstrator/ + lieferanten/ stacks
scripts/                         # operational simulator, eval harnesses, helpers
docs/                            # notes and diagrams
data/                            # gitignored: AAS corpora, ECLASS exports, samples
```

Install just what you need:

```bash
pip install neo4aas                # core: mapping + AASQL, neo4j driver only
pip install "neo4aas[basyx]"       # + Neo4jObjectStore
pip install "neo4aas[server]"      # + AAS Repository server
pip install "neo4aas[chatbot]"     # + the LangGraph chatbot (no basyx)
```

---

## How It Works

### Import (JSON → Neo4j)

```
AAS JSON file
    └─→ AASNeo4JClient.upload_json_file()
            └─→ JsonToNeo4jImporter._process_dict()
                    ├─→ Scalars become node properties
                    ├─→ Dicts become child nodes (edge named after the attribute)
                    └─→ Lists become multiple nodes with list_index tracking
                            └─→ APOC batch create nodes + relationships
```

### Export (Neo4j → JSON)

```
Neo4j subgraph
    └─→ JsonFromNeo4jExporter.convert_subgraph_to_data_dict()
            ├─→ Rebuilds nested dict structure from relationships
            └─→ Reverses list-of-dicts flattening
```

### Query (AASQL → Cypher)

```
AASQL JSON query
    └─→ parse_aasql_query()        # aasql_to_ast.py
            └─→ AST (ast_nodes.py)
                    └─→ converter()  # ast_to_cypher.py
                            └─→ MATCH ... WHERE ... RETURN Cypher string
```

---

## AAS → Neo4j Mapping

| AAS concept | Neo4j representation |
|---|---|
| `Referable` | Node |
| `AssetInformation` | Node |
| Containment (Referable → Referable) | semantic edge named after the attribute (`:value`, `:submodelElements`, `:statements`, …); list members carry `list_index` |
| `Reference` | `:references` edge (materialized by `resolve_references()`) |
| Scalar property | Node property |

### Multi-label Inheritance

Each node carries its full AAS class hierarchy as labels. For example, a `Property` node gets labels:

```
:Property:DataElement:SubmodelElement:Referable:Qualifiable
```

This allows Cypher queries to match on any level of the hierarchy.

### Dict/List-of-Dicts Flattening

Neo4j does not support dict-valued properties. Lists of dicts (e.g., `keys = [{"type": "...", "value": "..."}]`) are stored as parallel lists:

```
keys_type  = ["GlobalReference", ...]
keys_value = ["0173-1#01-AAO677#002", ...]
```

The shared positional index enables reconstruction during export.

### Deduplication

`Reference` and `ConceptDescription` nodes are deduplicated using SHA256 hashing of their properties. This prevents duplicate semantic identifiers across multiple AAS imports. Dedup is enforced at the database level: such nodes are MERGEd on their `hash` (and relationships are MERGEd too), so identical nodes imported by separate client instances/processes converge to one canonical node instead of being duplicated.

### Reference Resolution

A `ModelReference` stores its target only as key values (e.g. `keys_value = ["urn:sm/1", "Color"]`), not as a graph edge. To let queries *follow* a reference (for example "all AAS whose submodel matches …"), the adapter materializes a `:references` edge from each `ModelReference` node to the **target Referable** it points at — `keys_value[0]` selects the entry `Identifiable` by `id`, then the remaining keys descend to the actual target:

```
(:Reference {type:'ModelReference'})-[:references]->(:Referable)
```

This is driven from the application layer (the chosen approach), idempotent and order-independent (a reference added before its target is linked once the target appears):

- `AASNeo4JClient.resolve_references()` rebuilds **all** edges — use it once after a bulk import that bypasses the object store.
- `AASNeo4JClient.resolve_references_for(id)` is **incremental** — it re-resolves only references inside that Identifiable's subgraph plus references *targeting* it (found via an indexed `target_id == keys_value[0]` lookup). `Neo4jObjectStore.add` / `commit` call this. `discard` / `remove` need no resolution because `DETACH DELETE` drops every `:references` edge into the deleted subtree.

The full key chain is followed, so a `ModelReference` resolves to its actual target Referable — not just the top-level Identifiable. Each descending key is matched as an `idShort` (under a `SubmodelElementCollection` / `Submodel`) **or** as a 0-based list index (under a `SubmodelElementList`):

```
child.idShort = key  OR  edge.list_index = toInteger(key)
```

> **Alternative — APOC triggers.** The same resolution Cypher could instead run inside a DB-native `apoc.trigger` so that *out-of-band* writes (e.g. edits via the Neo4j Browser) are also maintained automatically. That requires `apoc.trigger.enabled=true` in `neo4j.conf` and is harder to test, so the application-layer approach is used here.

---

## AAS Query Language (AASQL)

AASQL queries are JSON documents compiled to Cypher via `convert_aasql_to_cypher()`.

### Roots

| AASQL root | Cypher pattern |
|---|---|
| `$aas` | `(aas:AssetAdministrationShell)` |
| `$sm` | `(sm:Submodel)` |
| `$cd` | `(cd:ConceptDescription)` |
| `$sme.<idShort>` | SubmodelElement traversal by idShort |

### Field path syntax

```
$<root>#<attribute>[.<nested>]
```

Examples: `$aas#idShort`, `$aas#assetInformation.assetType`, `$sme.Color#value`

A `$sme` with **no idShort path** (e.g. `$sme#value`) searches **all** SubmodelElements at any depth (recursive traversal over the containment edges), per the spec.

> The recursive form currently expands from each Submodel before filtering. For large graphs this may later be optimized to start from the matching SubmodelElement and walk back up (see Improvements.md #9).

### MultiLanguageProperty

`#value` works for both `Property` and `MultiLanguageProperty`. A `Property` stores a scalar value; an MLP stores text per language. `#value` matches the text in **any** language (`$sme.Note#value $contains "Hal"`), and `#language` filters by language code (`$sme.Note#language $eq "nl"`). All operators (`$eq`, `$contains`, `$starts-with`, `$regex`, …) apply.

> **Note:** combining `#value` and `#language` in a `$match` is **not** correlated to the same language entry yet — it matches if some text *and* some language satisfy the conditions independently.

### Cross-root queries ($aas + $sm/$sme)

A query may combine roots. When `$aas` is mixed with `$sm`/`$sme`, the compiler scopes the submodel conditions to the matching AAS's submodels by bridging through the `:references` edge (see [Reference Resolution](#reference-resolution)):

```cypher
MATCH (aas)-[:submodels]->(:Reference)-[:references]->(sm)
```

so the query returns "all AAS whose (referenced) submodel satisfies the condition" — not a cartesian product. The `RETURN` variable defaults to the outermost root (precedence `aas > sm > cd`, so an `$aas`+`$sme` query returns the AAS). Pass `convert_aasql_to_cypher(query, target="sm")` to force the returned type for a given endpoint (e.g. a Submodel repository).

### Supported operators

| Category | Operators |
|---|---|
| Comparison | `$eq`, `$ne`, `$gt`, `$ge`, `$lt`, `$le` |
| String | `$contains`, `$starts-with`, `$ends-with`, `$regex` |
| Logical | `$and`, `$or`, `$not` |
| List match | `$match` (all conditions on same list element) |
| Type casts | `$strCast`, `$numCast`, `$hexCast`, `$boolCast`, `$dateTimeCast`, `$timeCast` |

### Example

**AASQL query:**

```json
{
  "$condition": {
    "$and": [
      { "$eq": [{ "$field": "$sme.Color#value" }, { "$strVal": "Blue" }] },
      { "$gt": [{ "$field": "$sme.Size#value"  }, { "$numVal": 50 }] }
    ]
  }
}
```

**Generated Cypher:**

```cypher
MATCH (sme_Color:SubmodelElement {idShort: "Color"})
MATCH (sme_Size:SubmodelElement {idShort: "Size"})
WHERE sme_Color.value IN ["Blue"] AND sme_Size.value > 50
RETURN sme_Color, sme_Size
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- Neo4j Community Edition (tested with 5.26.x)
- [APOC plugin](https://neo4j.com/labs/apoc/) enabled in Neo4j

### Install

```bash
uv sync --all-extras      # or: make install
```

`make help` lists the common tasks (test, lint, build, up/down for the demonstrator stack).

### Start Neo4j

```bash
# Linux/Mac
$NEO4J_HOME/bin/neo4j console

# Windows
%NEO4J_HOME%\bin\neo4j console
```

Default bolt URI: `bolt://localhost:7687`

### Import an AAS file

```python
from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG

client = AASNeo4JClient(
    uri="bolt://localhost:7687",
    user="neo4j",
    password="12345678",
    model_config=AAS_NEO4J_MODEL_CONFIG
)
client.upload_json_file("path/to/your_aas.json")
```

### Explore in Neo4j Browser

```cypher
MATCH (n) RETURN n;
```

### Translate an AASQL query

```python
from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher

query = {
    "$condition": {
        "$eq": [{"$field": "$aas#idShort"}, {"$strVal": "MyShell"}]
    }
}
cypher = convert_aasql_to_cypher(query)
print(cypher)
```

---

## Testing

```bash
make test-unit           # no database needed
make test                # everything
make test-integration    # only the tests that need Neo4j
```

`tests/` mirrors `src/neo4aas/`, so a subtree runs on its own: `uv run pytest tests/core/query`.

Integration tests are marked `@pytest.mark.integration`. By default they start a
disposable `neo4j:5` container (testcontainers); set `NEO4J_URI` to run against an
existing instance instead — the fixtures wipe that database, so point it only at a
throwaway.

---

## Constraint Validation

`AASConstraintChecker` validates AAS data already loaded in Neo4j against the AAS specification constraints. It runs Cypher queries and returns structured `ConstraintViolation` records grouped in a `ConstraintReport`.

```python
from neo4aas.core.validation import AASConstraintChecker

checker = AASConstraintChecker(
    uri="bolt://localhost:7687",
    user="neo4j",
    password="12345678",
)
report = checker.check_all()
print(report.summary())
```

### Implemented constraints

| Constraint | Description |
|---|---|
| AASd-002 | `idShort` must match `[a-zA-Z][a-zA-Z0-9_-]*[a-zA-Z0-9_]+` |
| AASd-005 | `revision` requires `version` in `AdministrativeInformation` |
| AASd-014 | `SelfManagedEntity` must have `globalAssetId` or `specificAssetId` |
| AASd-021 | Qualifier `type` must be unique within a `Qualifiable` |
| AASd-022 | `idShort` must be unique within the parent namespace |
| AASd-077 | Extension `name` must be unique within a parent |
| AASd-107 | `SubmodelElementList` child `semanticId` must match `semanticIdListElement` |
| AASd-108 | `SubmodelElementList` children must match `typeValueListElement` |
| AASd-109 | `valueTypeListElement` required when `typeValueListElement` is `Property`/`Range` |
| AASd-114 | All `SubmodelElementList` children with `semanticId` must share the same one |
| AASd-117 | Non-`Identifiable` `Referable`s not under `SubmodelElementList` must have `idShort` |
| AASd-118 | `supplementalSemanticIds` requires a `semanticId` |
| AASd-119 | `TemplateQualifier` requires element `kind = "Template"` |
| AASd-121 – 129 | Reference key type and ordering constraints |
| AASd-131 | `AssetInformation` must have `globalAssetId` or at least one `specificAssetId` |
| AASd-133 | `SpecificAssetId.externalSubjectId` must be an `ExternalReference` |
| AASd-134 | Operation variable `idShort`s must be unique across in/out/inout |

---

## MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server exposes the Neo4j-backed graph to MCP clients (Claude Desktop, Claude Code, ...), so the data can be read, queried and validated in natural language.

### Install

```bash
pip install ".[mcp]"
```

### Run

```bash
python -m neo4aas.mcp      # or: neo4aas-mcp
```

Connection is configured via environment variables (defaults shown):

| Variable | Default |
|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | `12345678` |

### Tools

| Tool | Description |
|---|---|
| `count_stats` | Count of AssetAdministrationShells / Submodels / ConceptDescriptions (health check) |
| `get_identifiable` | Fetch an AAS / Submodel / ConceptDescription by `id` |
| `get_referable` | Fetch a Referable by parent `id` + `idShortPath` |
| `validate_constraints` | Run AAS spec constraint validation, returning a report |
| `list_submodel_types` | Distinct Submodel types (idShort + semanticId) with instance count each |
| `list_submodel_types_by_semantic_id` | Distinct Submodel semanticIds (idShort ignored) with instance count each |
| `abstract_submodel` | Build a Template-kind structural union from all Submodels of a given type |

All tools are read-only; none mutate the graph.

#### `abstract_submodel` — type matching and output format

`submodel_type` is matched against `idShort` by default. If the value contains `://` or starts with `urn:`, it is matched against the Submodel's `semanticId` instead:

```
abstract_submodel("DigitalNameplate")
abstract_submodel("https://admin-shell.io/zvei/nameplate/2/0/Nameplate")
abstract_submodel("DigitalNameplate", output_format="yaml")
```

### Claude Desktop config

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "neo4aas": {
      "command": "python",
      "args": ["-m", "neo4aas.mcp"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "12345678"
      }
    }
  }
}
```

---

## Limitations

- **Export is partial**: `JsonFromNeo4jExporter` reconstructs basic nested structures but does not fully restore all AAS-specific semantics.
- **Reference resolution**: `ModelReference` and `ExternalReference` targets are stored but not resolved/traversed automatically.
- **ECLASS**: ECLASS classifications are stored as properties; they are not modeled as a separate node graph.
- **No authentication management**: Connection credentials are passed directly; no secrets management is included.
- **Deduplication ignores `referredSemanticId`**: The SHA256 hash used to deduplicate `Reference` nodes covers only flat properties. Two References that differ solely in their `referredSemanticId` relationship are collapsed into one node, which may create graph cycles. The exporter detects and breaks these cycles with a warning, but round-trip fidelity for such elements is lost.

---

## Funding
This open-source project was developed within the **Wind-X** project.
This project has received public funding from the **European Union** NextGenerationEU within the Important Project of Common European Interest – Cloud Infrastructures and Services (IPCEI-CIS) under grant agreement 13IPC037G.

<p align="center">
  <img alt="Bundesministerium für Wirtschaft und Energie (BMWE)-EU funding logo" src="docs/images/logo_sponsored_funding.png" width="400"/>
</p>
