"""A flattened list property with one entry is stored as a scalar.

Flattening a list-of-dicts produces parallel list properties (`description` ->
`description_text` + `description_language`). 87 % of them hold a single entry, and a
one-entry array is not cheap in Neo4j: it lives in the dynamic array store and measures
~4x the cost of the same value as a scalar (see docs/storage-optimisation.md). The
encoding is lossless because these properties are *always* derived from a JSON array, so a
scalar unambiguously means "one entry" — the exporter wraps it back.
"""
import pytest

from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG, AASNeo4JClient


@pytest.fixture
def client() -> AASNeo4JClient:
    return AASNeo4JClient(uri=None, user="x", model_config=AAS_NEO4J_MODEL_CONFIG)


def _node(nodes, label):
    return next(n for n in nodes if label in n["labels"])


def test_single_entry_list_is_stored_as_a_scalar(client):
    nodes, _ = client._process_dict({
        "modelType": "Property", "idShort": "P", "valueType": "xs:string", "value": "x",
        "description": [{"language": "en", "text": "Only one"}],
    })
    node = _node(nodes, "Property")
    assert node["description_text"] == "Only one"
    assert node["description_language"] == "en"


def test_multi_entry_list_keeps_its_list(client):
    nodes, _ = client._process_dict({
        "modelType": "Property", "idShort": "P", "valueType": "xs:string", "value": "x",
        "description": [{"language": "en", "text": "One"}, {"language": "de", "text": "Eins"}],
    })
    node = _node(nodes, "Property")
    assert node["description_text"] == ["One", "Eins"]
    assert node["description_language"] == ["en", "de"]


def test_reference_keys_keep_their_list_encoding(client):
    """`keys_value` is read as a list by the AASQL compiler, the validation queries and the
    agent tools, and Reference nodes are deduplicated to a tiny population anyway — so it is
    deliberately not compacted."""
    nodes, _ = client._process_dict({
        "type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": "0173-1#02-AAO677#002"}],
    })
    node = _node(nodes, "Reference")
    assert node["keys_value"] == ["0173-1#02-AAO677#002"]
    assert node["target_id"] == "0173-1#02-AAO677#002"


def test_multilanguage_property_value_is_compacted(client):
    nodes, _ = client._process_dict({
        "modelType": "MultiLanguageProperty", "idShort": "M",
        "value": [{"language": "en", "text": "Pump"}],
    })
    node = _node(nodes, "MultiLanguageProperty")
    assert node["value_text"] == "Pump"
    assert node["value_language"] == "en"


@pytest.mark.parametrize("stored, expected", [
    ("Only one", [{"language": "en", "text": "Only one"}]),
    (["One", "Eins"], [{"language": "en", "text": "One"}, {"language": "de", "text": "Eins"}]),
])
def test_export_restores_the_list(client, stored, expected):
    """Both encodings must export as the AAS-JSON list — a store written before the change
    keeps working."""
    languages = "en" if isinstance(stored, str) else ["en", "de"]
    props = {"idShort": "P", "description_text": stored, "description_language": languages}
    out = client._merge_prefixed_props_back_to_list_of_dicts_prop(
        ["Property", "Referable"], dict(props))
    assert out["description"] == expected
