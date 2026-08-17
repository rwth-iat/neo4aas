import json
import pytest
from pathlib import Path

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import IDENTIFIABLE_KEYS, AASNeo4JClient
from aas_mapping.aas_neo4j_adapter.xmlification.xml_to_json import xml_to_aas_json

from tests.paths import (
    SUBMODELS_DIR as _EXAMPLES_DIR,
    EXPECTED_XML_DIR as _XML_TEST_DATA_DIR,
    EXPECTED_JSON_DIR as _JSON_TEST_DATA_DIR,
)

# Files with known exporter bugs — tracked in TODOs.md
_XFAIL_STEMS = {
    # Deduplication merges Reference nodes that differ only in referredSemanticId (a nested
    # relationship), producing graph cycles. The exporter handles cycles without crashing but
    # cannot fully reconstruct the original structure.
    "IDTA 02056-1-0_Template_Data Retention Policies",
}

def _normalize(value):
    """
    Recursively remove keys whose values are None or empty collections/strings,
    and sort lists of dicts for order-insensitive comparison.

    Many AAS list relationships (qualifiers, submodels, description, etc.) do not
    carry a list_index in Neo4j and may be returned in arbitrary order; this is
    tracked as a bug in TODOs.md.

    Applied symmetrically to both the exported and original dicts.
    """
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
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


def _collect_json_files() -> list[Path]:
    files = sorted(_EXAMPLES_DIR.glob("*.json"))
    if _JSON_TEST_DATA_DIR.exists():
        files += sorted(_JSON_TEST_DATA_DIR.rglob("*.json"))
    return files


def _collect_xml_files() -> list[Path]:
    if not _XML_TEST_DATA_DIR.exists():
        return []
    return sorted(_XML_TEST_DATA_DIR.rglob("*.xml"))


def _xml_to_json_path(xml_file: Path) -> Path:
    """Map an Expected_XML file path to the corresponding Expected_JSON file path.

    Directory names differ only in the first character's case:
      Expected_XML/assetAdministrationShell/maximal.xml
      → Expected_JSON/AssetAdministrationShell/maximal.json
    """
    rel = xml_file.relative_to(_XML_TEST_DATA_DIR)
    parts = list(rel.parts)
    parts[0] = parts[0][0].upper() + parts[0][1:]
    return _JSON_TEST_DATA_DIR.joinpath(*parts).with_suffix(".json")


def _collect_xml_json_pairs() -> list[tuple[Path, Path]]:
    """Return (xml_file, json_file) pairs where both counterparts exist."""
    pairs = []
    for xml_file in _collect_xml_files():
        json_file = _xml_to_json_path(xml_file)
        if json_file.exists():
            pairs.append((xml_file, json_file))
    return pairs


@pytest.mark.integration
@pytest.mark.parametrize("json_file", _collect_json_files(), ids=lambda p: p.stem)
def test_json_roundtrip(json_file: Path, aas_client: AASNeo4JClient):
    if json_file.stem in _XFAIL_STEMS:
        pytest.xfail("Known exporter bug — see TODOs.md")

    with open(json_file, encoding="utf-8") as f:
        env = json.load(f)

    aas_client.upload_json(env)

    for section in IDENTIFIABLE_KEYS:
        for original in env.get(section, []):
            ident_id = original["id"]
            retrieved = aas_client.get_identifiable(ident_id)
            assert _normalize(retrieved) == _normalize(original), (
                f"Round-trip mismatch for {ident_id!r} in {json_file.name}"
            )


@pytest.mark.integration
def test_get_submodels_by_type_matches_get_identifiable(aas_client: AASNeo4JClient):
    """get_submodels_by_type must reconstruct the same submodel as get_identifiable.

    Guards the perf optimization that uses the relationships yielded by
    apoc.path.subgraphAll directly (instead of recomputing them with an
    OPTIONAL MATCH over every node pair): both paths must produce the identical
    AAS JSON for a real submodel.
    """
    json_file = _EXAMPLES_DIR / "IDTA 02002-1-0_Template_ContactInformation.json"
    with open(json_file, encoding="utf-8") as f:
        env = json.load(f)
    aas_client.upload_json(env)

    sm = env["submodels"][0]
    by_id = aas_client.get_identifiable(sm["id"])
    by_type = aas_client.get_submodels_by_type(sm["idShort"], by_semantic_id=False)

    assert len(by_type) == 1
    assert _normalize(by_type[0]) == _normalize(by_id)


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
        for section in IDENTIFIABLE_KEYS
        for obj in env.get(section, [])
        if "id" in obj
    ]
    if not identifiables:
        pytest.skip(f"No top-level Identifiable in {xml_file}")

    aas_client.upload_xml(env)

    for _, original in identifiables:
        retrieved = aas_client.get_identifiable(original["id"])
        assert _normalize(retrieved) == _normalize(original), (
            f"Round-trip mismatch for {original['id']!r} in {xml_file}"
        )
