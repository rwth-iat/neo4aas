# aas4graph

A proof-of-concept library for mapping [Asset Administration Shell (AAS)](https://industrialdigitaltwin.org/content-hub/aasspecifications) data to a [Neo4j](https://neo4j.com/) graph database, including bidirectional serialization and a query language compiler (AASQL → Cypher).

---

## Architecture

```
aas_mapping/
├── aas_neo4j_adapter/
│   ├── aas_neo4j_client.py      # Main API: AASNeo4JClient
│   ├── base.py                  # BaseNeo4JClient, Neo4jModelConfig
│   ├── utils.py                 # UploadStats, hash helpers
│   ├── validation.py            # AASConstraintChecker — spec constraint validation
│   ├── neo_aas_object_store.py  # basyx-sdk AbstractObjectStore for Neo4j
│   ├── jsonification/
│   │   ├── neo4j_import.py      # JSON → Neo4j (JsonToNeo4jImporter)
│   │   └── neo4j_export.py      # Neo4j → JSON (JsonFromNeo4jExporter)
│   └── querification/
│       ├── aasql_to_ast.py      # AASQL JSON → AST (parser)
│       ├── ast_nodes.py         # AST node type definitions
│       ├── ast_to_cypher.py     # AST → Cypher (compiler)
│       └── aasql_to_cypher.py   # Entry point: convert_aasql_to_cypher()
├── examples/
│   ├── queries/                 # Example AASQL queries (JSON)
│   ├── ast/                     # Expected AST representations
│   ├── cypher/                  # Generated Cypher scripts
│   └── submodels/               # IDTA template submodel JSON files
└── test/
    ├── test_aasql2ast.py        # Unit tests for AASQL → AST parsing
    ├── test_roundtrip.py        # Integration: JSON ↔ Neo4j round-trip tests
    └── test_constraint_checker.py  # Unit + integration tests for AASConstraintChecker
```

---

## How It Works

### Import (JSON → Neo4j)

```
AAS JSON file
    └─→ AASNeo4JClient.upload_json_file()
            └─→ JsonToNeo4jImporter._process_dict()
                    ├─→ Scalars become node properties
                    ├─→ Dicts become child nodes (CHILD relationship)
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
| Containment (Referable → Referable) | `CHILD` relationship |
| `Reference` | `REFERENCES` relationship |
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

`Reference` and `ConceptDescription` nodes are deduplicated using SHA256 hashing of their properties. This prevents duplicate semantic identifiers across multiple AAS imports.

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

- Python 3.10+
- Neo4j Community Edition (tested with 5.26.x)
- [APOC plugin](https://neo4j.com/labs/apoc/) enabled in Neo4j

### Install

```bash
pip install .
```

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
from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG

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
from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import convert_aasql_to_cypher

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
uv run pytest aas_mapping/test/
```

Integration tests require a live Neo4j instance (`bolt://localhost:7687`, user `neo4j`, password `12345678`). They are skipped automatically if Neo4j is unreachable.

---

## Constraint Validation

`AASConstraintChecker` validates AAS data already loaded in Neo4j against the AAS specification constraints. It runs Cypher queries and returns structured `ConstraintViolation` records grouped in a `ConstraintReport`.

```python
from aas_mapping.aas_neo4j_adapter.validation import AASConstraintChecker

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

## Limitations

- **Export is partial**: `JsonFromNeo4jExporter` reconstructs basic nested structures but does not fully restore all AAS-specific semantics.
- **Reference resolution**: `ModelReference` and `ExternalReference` targets are stored but not resolved/traversed automatically.
- **ECLASS**: ECLASS classifications are stored as properties; they are not modeled as a separate node graph.
- **No authentication management**: Connection credentials are passed directly; no secrets management is included.
- **Deduplication ignores `referredSemanticId`**: The SHA256 hash used to deduplicate `Reference` nodes covers only flat properties. Two References that differ solely in their `referredSemanticId` relationship are collapsed into one node, which may create graph cycles. The exporter detects and breaks these cycles with a warning, but round-trip fidelity for such elements is lost.
