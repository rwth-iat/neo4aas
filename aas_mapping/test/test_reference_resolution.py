"""Integration tests for automatic ModelReference -> Identifiable resolution.

These create ``:references`` edges from each ``ModelReference`` node to the
``Identifiable`` it points at (matched on ``keys_value[0]`` == ``id``). They run
against a live Neo4j and are skipped automatically when it is unreachable.
"""
import pytest

from aas_mapping.aas_neo4j_adapter.neo_aas_object_store import Neo4jObjectStore

pytestmark = pytest.mark.integration


def _submodel(sm_id: str, id_short: str = "SM1") -> dict:
    return {"modelType": "Submodel", "id": sm_id, "idShort": id_short}


def _aas_referencing(aas_id: str, sm_id: str, id_short: str = "AAS1") -> dict:
    return {
        "modelType": "AssetAdministrationShell",
        "id": aas_id,
        "idShort": id_short,
        "assetInformation": {"assetKind": "Instance"},
        "submodels": [
            {"type": "ModelReference", "keys": [{"type": "Submodel", "value": sm_id}]}
        ],
    }


def _references_edge_count(client, aas_id: str, sm_id: str) -> int:
    clause = (
        "MATCH (aas:AssetAdministrationShell {id: $aas_id})"
        "-[:submodels]->(:Reference)-[:references]->(sm:Submodel {id: $sm_id}) "
        "RETURN count(*) AS c"
    )
    with client.driver.session() as session:
        return session.run(clause, aas_id=aas_id, sm_id=sm_id).single()["c"]


def test_resolve_references_creates_edge(aas_client):
    aas_client.add_identifiable(_submodel("urn:sm/1"))
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))

    created = aas_client.resolve_references()

    assert created >= 1
    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1


def test_resolve_is_order_independent(aas_client):
    # AAS added first -> its submodel Reference is initially dangling.
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))
    aas_client.resolve_references()
    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 0

    # Target appears later -> resolution links it.
    aas_client.add_identifiable(_submodel("urn:sm/1"))
    aas_client.resolve_references()
    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1


def test_resolve_is_idempotent(aas_client):
    aas_client.add_identifiable(_submodel("urn:sm/1"))
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))

    aas_client.resolve_references()
    aas_client.resolve_references()

    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1


def test_object_store_add_resolves_automatically(aas_client):
    from basyx.aas import model

    store = Neo4jObjectStore(aas_client)
    sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
    aas = model.AssetAdministrationShell(
        id_="urn:aas/1",
        id_short="AAS1",
        asset_information=model.AssetInformation(asset_kind=model.AssetKind.INSTANCE,
                                                 global_asset_id="urn:asset/1"),
        submodel={model.ModelReference.from_referable(sm)},
    )
    store.add(sm)
    store.add(aas)

    # No explicit resolve_references() call: the store must maintain it.
    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1


def _resolved_target_idshort(client, keys_value: list[str]) -> list:
    clause = (
        "MATCH (r:Reference)-[:references]->(t) WHERE r.keys_value = $kv "
        "RETURN t.idShort AS id_short, t.value AS value"
    )
    with client.driver.session() as session:
        return [(rec["id_short"], rec["value"]) for rec in session.run(clause, kv=keys_value)]


def test_deep_resolution_to_collection_member_by_idshort(aas_client):
    sm = {
        "modelType": "Submodel",
        "id": "urn:sm/1",
        "idShort": "SM1",
        "submodelElements": [
            {
                "modelType": "SubmodelElementCollection",
                "idShort": "Specs",
                "value": [
                    {"modelType": "Property", "idShort": "Color", "valueType": "xs:string", "value": "red"}
                ],
            },
            {
                "modelType": "ReferenceElement",
                "idShort": "Ref",
                "value": {
                    "type": "ModelReference",
                    "keys": [
                        {"type": "Submodel", "value": "urn:sm/1"},
                        {"type": "SubmodelElementCollection", "value": "Specs"},
                        {"type": "Property", "value": "Color"},
                    ],
                },
            },
        ],
    }
    aas_client.add_identifiable(sm)
    aas_client.resolve_references()

    targets = _resolved_target_idshort(aas_client, ["urn:sm/1", "Specs", "Color"])
    assert targets == [("Color", "red")]


def test_deep_resolution_to_list_member_by_index(aas_client):
    sm = {
        "modelType": "Submodel",
        "id": "urn:sm/2",
        "idShort": "SM2",
        "submodelElements": [
            {
                "modelType": "SubmodelElementList",
                "idShort": "Items",
                "typeValueListElement": "Property",
                "valueTypeListElement": "xs:string",
                "value": [
                    {"modelType": "Property", "valueType": "xs:string", "value": "a"},
                    {"modelType": "Property", "valueType": "xs:string", "value": "b"},
                ],
            },
            {
                "modelType": "ReferenceElement",
                "idShort": "Ref2",
                "value": {
                    "type": "ModelReference",
                    "keys": [
                        {"type": "Submodel", "value": "urn:sm/2"},
                        {"type": "SubmodelElementList", "value": "Items"},
                        {"type": "Property", "value": "1"},
                    ],
                },
            },
        ],
    }
    aas_client.add_identifiable(sm)
    aas_client.resolve_references()

    # Index 1 -> the second list element (value "b").
    targets = _resolved_target_idshort(aas_client, ["urn:sm/2", "Items", "1"])
    assert targets == [(None, "b")]


def test_resolve_removes_stale_edge_on_retarget(aas_client):
    aas_client.add_identifiable(_submodel("urn:sm/1"))
    aas_client.add_identifiable(_submodel("urn:sm/2", id_short="SM2"))
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))
    aas_client.resolve_references()
    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1

    # Retarget the AAS submodel reference to sm/2.
    aas_client.execute_clause(
        "MATCH (:AssetAdministrationShell {id: 'urn:aas/1'})-[:submodels]->(r:Reference) "
        "SET r.keys_value = ['urn:sm/2']"
    )
    aas_client.resolve_references()

    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 0
    assert _references_edge_count(aas_client, "urn:aas/1", "urn:sm/2") == 1
