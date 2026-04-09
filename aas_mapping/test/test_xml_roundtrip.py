import json
import pytest
from pathlib import Path

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import IDENTIFIABLE_KEYS, AASNeo4JClient
from aas_mapping.aas_neo4j_adapter.xmlification.xml_to_json import xml_to_aas_json

_XML_TEST_DATA_DIR = Path(__file__).parent / "test_data" / "Expected_XML"
_JSON_TEST_DATA_DIR = Path(__file__).parent / "test_data" / "Expected"

_IDENTIFIABLE_KEYS = ("assetAdministrationShells", "submodels", "conceptDescriptions")

# TODO: Define internal keys in one place and import here, to avoid duplication between test modules
_INTERNAL_KEYS = frozenset({"uid", "hash"})


def _collect_xml_files() -> list[Path]:
    if not _XML_TEST_DATA_DIR.exists():
        return []
    return sorted(_XML_TEST_DATA_DIR.rglob("*.xml"))


def _xml_to_json_path(xml_file: Path) -> Path:
    """Map an Expected_XML file path to the corresponding Expected JSON file path.

    Directory names differ only in the first character's case:
      Expected_XML/assetAdministrationShell/maximal.xml
      → Expected/AssetAdministrationShell/maximal.json
    """
    rel = xml_file.relative_to(_XML_TEST_DATA_DIR)
    parts = list(rel.parts)
    parts[0] = parts[0][0].upper() + parts[0][1:]
    return _JSON_TEST_DATA_DIR.joinpath(*parts).with_suffix(".json")


def _collect_xml_json_pairs() -> list[tuple[Path, Path]]:
    """Return (xml_file, json_file) pairs where both counterparts exist."""
    pairs = []
    for xml_file in sorted(_XML_TEST_DATA_DIR.rglob("*.xml")):
        json_file = _xml_to_json_path(xml_file)
        if json_file.exists():
            pairs.append((xml_file, json_file))
    return pairs


def _normalize(value):
    """Strip Neo4j-internal keys and make comparison order-insensitive.

    Identical to the normalization used in test_roundtrip.py so that XML and JSON
    roundtrip tests are comparable.
    """
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if k in _INTERNAL_KEYS:
                continue
            normalized = _normalize(v)
            if normalized is None or normalized == [] or normalized == {} or normalized == "":
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


@pytest.mark.parametrize(
    "xml_file,json_file",
    _collect_xml_json_pairs(),
    ids=lambda p: f"{p.parent.name}/{p.stem}",
)
def test_xml_to_json_matches_expected(xml_file: Path, json_file: Path):
    """xml_to_aas_json() output must match the canonical JSON representation.

    Loads an AAS XML file and its JSON counterpart from the test data, normalises
    both with _normalize(), and asserts equality. No Neo4j required.
    """
    from_xml = xml_to_aas_json(str(xml_file))
    with open(json_file, encoding="utf-8") as f:
        from_json = json.load(f)

    assert _normalize(from_xml) == _normalize(from_json), (
        f"xml_to_aas_json({xml_file.name}) does not match {json_file.name}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("xml_file", _collect_xml_files(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_xml_roundtrip(xml_file: Path, aas_client: AASNeo4JClient):
    env = xml_to_aas_json(str(xml_file))

    identifiables = [
        (section, obj)
        for section in _IDENTIFIABLE_KEYS
        for obj in env.get(section, [])
        if "id" in obj
    ]
    if not identifiables:
        pytest.skip(f"No top-level Identifiable in {xml_file}")

    aas_client.upload_xml(env)

    for _section, original in identifiables:
        retrieved = aas_client.get_identifiable(original["id"])
        assert _normalize(retrieved) == _normalize(original), (
            f"Round-trip mismatch for {original['id']!r} in {xml_file}"
        )
