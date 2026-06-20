"""Integration tests for containment navigation after removing the :child edge.

Containment is represented only by the semantic edges (named after the attribute,
e.g. :value, :submodelElements). `_find_node` traverses those, matching each step
by idShort (Collection/Submodel member) or list_index (SubmodelElementList member).

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

pytestmark = pytest.mark.integration


def _collection_submodel() -> dict:
    return {
        "modelType": "Submodel",
        "id": "urn:sm/coll",
        "idShort": "SMcoll",
        "submodelElements": [
            {
                "modelType": "SubmodelElementCollection",
                "idShort": "Specs",
                "value": [
                    {"modelType": "Property", "idShort": "Color", "valueType": "xs:string", "value": "red"}
                ],
            }
        ],
    }


def _list_submodel() -> dict:
    return {
        "modelType": "Submodel",
        "id": "urn:sm/list",
        "idShort": "SMlist",
        "submodelElements": [
            {
                "modelType": "SubmodelElementList",
                "idShort": "Items",
                "typeValueListElement": "SubmodelElementCollection",
                "value": [
                    {
                        "modelType": "SubmodelElementCollection",
                        "value": [
                            {"modelType": "Property", "idShort": "Inner", "valueType": "xs:string", "value": "a"}
                        ],
                    }
                ],
            }
        ],
    }


def _scalar(client, clause: str, **params):
    with client.driver.session() as session:
        rec = session.run(clause, **params).single()
        return rec[0] if rec else None


def test_no_child_edges_created(aas_client):
    aas_client.add_identifiable(_collection_submodel())
    assert _scalar(aas_client, "MATCH ()-[r:child]->() RETURN count(r)") == 0
    # Containment still present via the semantic edge.
    assert _scalar(
        aas_client,
        "MATCH (:Submodel)-[:submodelElements]->(:SubmodelElementCollection)-[:value]->(p:Property) RETURN count(p)",
    ) == 1


def test_find_node_under_collection(aas_client):
    aas_client.add_identifiable(_collection_submodel())

    node_id = aas_client._find_node("urn:sm/coll", "Specs.Color")
    value = _scalar(aas_client, "MATCH (n) WHERE elementId(n) = $id RETURN n.value", id=node_id)
    assert value == "red"


def test_find_node_under_list_by_index(aas_client):
    aas_client.add_identifiable(_list_submodel())

    # Items[0] -> the (idShort-less) collection; then .Inner.
    node_id = aas_client._find_node("urn:sm/list", "Items[0].Inner")
    value = _scalar(aas_client, "MATCH (n) WHERE elementId(n) = $id RETURN n.value", id=node_id)
    assert value == "a"


def test_add_submodel_element_under_collection(aas_client):
    aas_client.add_identifiable(_collection_submodel())

    new_prop = {"modelType": "Property", "idShort": "Size", "valueType": "xs:string", "value": "L"}
    aas_client.add_submodel_element(new_prop, "urn:sm/coll", "Specs")

    assert _scalar(
        aas_client,
        "MATCH (:SubmodelElementCollection {idShort:'Specs'})-[:value]->(p:Property {idShort:'Size'}) "
        "RETURN count(p)",
    ) == 1


def test_remove_referable_under_collection(aas_client):
    sm = _collection_submodel()
    sm["submodelElements"][0]["value"].append(
        {"modelType": "Property", "idShort": "Size", "valueType": "xs:string", "value": "L"}
    )
    aas_client.add_identifiable(sm)

    aas_client.remove_referable("urn:sm/coll", "Specs.Color")

    assert _scalar(aas_client, "MATCH (p:Property {idShort:'Color'}) RETURN count(p)") == 0
    assert _scalar(aas_client, "MATCH (p:Property {idShort:'Size'}) RETURN count(p)") == 1


def test_get_referable_with_path(aas_client):
    # Exercises _find_node_clause composed with apoc.path.subgraphAll (get_referable path).
    aas_client.add_identifiable(_collection_submodel())

    data = aas_client.get_referable("urn:sm/coll", "Specs")
    assert data["idShort"] == "Specs"
    assert any(v.get("idShort") == "Color" for v in data.get("value", []))
