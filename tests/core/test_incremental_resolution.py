"""Integration tests for incremental reference resolution and the target_id denormalization.

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

from basyx.aas import model

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


def _edge_count(client, aas_id: str, sm_id: str) -> int:
    clause = (
        "MATCH (:AssetAdministrationShell {id: $aas})-[:submodels]->(:Reference)"
        "-[:references]->(:Submodel {id: $sm}) RETURN count(*) AS c"
    )
    with client.driver.session() as session:
        return session.run(clause, aas=aas_id, sm=sm_id).single()["c"]


def _scalar(client, clause: str, **params):
    with client.driver.session() as session:
        rec = session.run(clause, **params).single()
        return rec[0] if rec else None


def test_target_id_set_to_first_key(aas_client):
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))
    assert _scalar(
        aas_client, "MATCH (r:Reference {type:'ModelReference'}) RETURN r.target_id"
    ) == "urn:sm/1"


def test_target_id_index_exists(aas_client):
    clause = "SHOW INDEXES YIELD labelsOrTypes, properties RETURN labelsOrTypes AS l, properties AS p"
    with aas_client.driver.session() as session:
        found = any(
            r["l"] == ["Reference"] and r["p"] == ["target_id"] for r in session.run(clause)
        )
    assert found


def test_target_id_stripped_on_export(aas_client):
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))
    data = aas_client.get_identifiable("urn:aas/1")
    # No internal key leaks into the exported submodels reference.
    for ref in data.get("submodels", []):
        assert "target_id" not in ref


def test_incremental_resolves_new_refs_in_subtree(aas_client):
    aas_client.add_identifiable(_submodel("urn:sm/1"))
    # Adding the AAS resolves its own (in-subtree) submodel reference.
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/1"))
    aas_client.resolve_references_for("urn:aas/1")

    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1


def test_incremental_resolves_dangling_ref_when_target_added(aas_client):
    # AAS added first: its reference is dangling (target absent).
    aas_client.add_identifiable(_aas_referencing("urn:aas/1", "urn:sm/late"))
    aas_client.resolve_references_for("urn:aas/1")
    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/late") == 0

    # Target appears later: resolving FOR the target must pick up the dangling ref
    # via the indexed target_id lookup (not a full scan).
    aas_client.add_identifiable(_submodel("urn:sm/late"))
    aas_client.resolve_references_for("urn:sm/late")
    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/late") == 1


def test_object_store_add_resolves_incrementally(aas_client):
    store = Neo4jObjectStore(aas_client)
    sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
    aas = model.AssetAdministrationShell(
        id_="urn:aas/1",
        id_short="AAS1",
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.INSTANCE, global_asset_id="urn:asset/1"
        ),
        submodel={model.ModelReference.from_referable(sm)},
    )
    # Add AAS first (dangling), then the target submodel — incremental add must link them.
    store.add(aas)
    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 0
    store.add(sm)
    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1


def test_object_store_discard_drops_edge(aas_client):
    store = Neo4jObjectStore(aas_client)
    sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
    aas = model.AssetAdministrationShell(
        id_="urn:aas/1",
        id_short="AAS1",
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.INSTANCE, global_asset_id="urn:asset/1"
        ),
        submodel={model.ModelReference.from_referable(sm)},
    )
    store.add(sm)
    store.add(aas)
    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 1

    # Removing the target drops the edge via DETACH DELETE (no explicit re-resolve).
    store.discard(sm)
    assert _edge_count(aas_client, "urn:aas/1", "urn:sm/1") == 0
