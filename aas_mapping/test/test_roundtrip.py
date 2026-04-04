import json
import pytest
from pathlib import Path

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient

_EXAMPLES_DIR = Path(__file__).parent.parent / "examples" / "submodels"
_TEST_DATA_DIR = Path(__file__).parent / "test_data"

_IDENTIFIABLE_KEYS = ("assetAdministrationShells", "submodels", "conceptDescriptions")

# Files with known exporter bugs — tracked in TODOs.md
_XFAIL_STEMS = {
    # referredSemanticId.keys not reconstructed (flattened keys_type/keys_value not reversed)
    "IDTA 02056-1-0_Template_Data Retention Policies",
    # Deduplicated Reference nodes shared across multiple parents within one Identifiable:
    # exporter only assigns the shared node to the first parent, leaving others as null.
    "IDTA 02011-1-1_Template_HSEBoM",
    "IDTA 02017-1-0_Template_Asset Interfaces Description",
    "IDTA 02027-1-0_Template_AIMC ",
    "IDTA 02045-1-0_Template_DataModelForAssetLocation",
}

_INTERNAL_KEYS = frozenset({"uid", "hash"})


def _collect_json_files() -> list[Path]:
    files = sorted(_EXAMPLES_DIR.glob("*.json"))
    if _TEST_DATA_DIR.exists():
        files += sorted(_TEST_DATA_DIR.rglob("*.json"))
    return files


def _normalize(value):
    """
    Recursively strip internal Neo4j artifact keys and remove keys whose
    values are None or empty collections.

    Lists of dicts are sorted by their serialized form so that the comparison
    is order-insensitive. Many AAS list relationships (qualifiers, submodels,
    description, etc.) do not carry a list_index in Neo4j and therefore may
    be returned in arbitrary order; this is tracked as a bug in TODOs.md.

    Applied symmetrically to both the exported and original dicts.
    """
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if k in _INTERNAL_KEYS:
                continue
            normalized = _normalize(v)
            if normalized is None or normalized == [] or normalized == {}:
                continue
            result[k] = normalized
        return result
    if isinstance(value, list):
        items = [_normalize(item) for item in value]
        items = [item for item in items if item is not None and item != {} and item != []]
        if items and isinstance(items[0], dict):
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
        return items
    return value


@pytest.mark.integration
@pytest.mark.parametrize("json_file", _collect_json_files(), ids=lambda p: p.stem)
def test_dict_roundtrip(json_file: Path, aas_client: AASNeo4JClient):
    if json_file.stem in _XFAIL_STEMS:
        pytest.xfail("Known exporter bug — see TODOs.md")

    with open(json_file, encoding="utf-8") as f:
        env = json.load(f)

    aas_client.upload_json(env)

    for section in _IDENTIFIABLE_KEYS:
        for original in env.get(section, []):
            ident_id = original["id"]
            retrieved = aas_client.get_identifiable(ident_id)
            assert _normalize(retrieved) == _normalize(original), (
                f"Round-trip mismatch for {ident_id!r} in {json_file.name}"
            )
