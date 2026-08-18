"""`modelType` is carried by the node labels, not by a duplicate string property.

Every AAS element already stores its class hierarchy as Neo4j labels, so a `modelType`
property repeats a label on every one of them — on corpus tier t100 that is ~69k property
slots, over a quarter of all node properties. The exporter restores it from the labels.
"""
import pytest

from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG


def test_model_type_is_not_stored_as_a_property_but_derived_from_labels():
    client = AASNeo4JClient(uri=None, user="x", model_config=AAS_NEO4J_MODEL_CONFIG)
    nodes, _ = client._process_dict(
        {"modelType": "Property", "idShort": "P", "valueType": "xs:string", "value": "x"}
    )
    node = nodes[-1]
    assert "modelType" not in node
    assert "Property" in node["labels"]

    restored = client._restore_derived_props(list(node["labels"]), {"idShort": "P"})
    assert restored["modelType"] == "Property"


@pytest.mark.parametrize(
    "model_type",
    ["Property", "AnnotatedRelationshipElement", "BasicEventElement", "Submodel",
     "ConceptDescription", "DataSpecificationIec61360"],
)
def test_most_specific_label_wins(model_type):
    """A subclass carries its parent's label too (AnnotatedRelationshipElement is also a
    RelationshipElement), so the restored modelType must be the most specific one."""
    client = AASNeo4JClient(uri=None, user="x", model_config=AAS_NEO4J_MODEL_CONFIG)
    labels = client.identify_labels({"modelType": model_type})
    assert client._restore_derived_props(list(labels), {})["modelType"] == model_type


def test_nodes_without_a_model_type_get_none():
    """A Reference / LangString / AssetInformation has no modelType in AAS JSON — the
    exporter must not invent one."""
    client = AASNeo4JClient(uri=None, user="x", model_config=AAS_NEO4J_MODEL_CONFIG)
    for obj in ({"type": "ExternalReference", "keys": []},
                {"language": "en", "text": "hi"},
                {"assetKind": "Instance"}):
        labels = client.identify_labels(obj)
        assert "modelType" not in client._restore_derived_props(list(labels), {})
