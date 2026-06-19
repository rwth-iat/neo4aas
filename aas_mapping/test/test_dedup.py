"""Integration tests for cross-import (DB-level) deduplication.

Reference / ConceptDescription nodes are MERGEd on their content hash, so an
identical node imported by a *different* client instance reuses the canonical
node already in the database instead of creating a duplicate. The in-memory
dedup map only covers a single client lifetime; these tests use two separate
clients on the same DB to prove the database-level behaviour.

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG

pytestmark = pytest.mark.integration


def _property_with_semantic_id(id_short: str, semantic_value: str) -> dict:
    return {
        "modelType": "Property",
        "idShort": id_short,
        "valueType": "xs:string",
        "value": "x",
        "semanticId": {
            "type": "ExternalReference",
            "keys": [{"type": "GlobalReference", "value": semantic_value}],
        },
    }


def _submodel(sm_id: str, id_short: str, prop: dict) -> dict:
    return {"modelType": "Submodel", "id": sm_id, "idShort": id_short, "submodelElements": [prop]}


def _second_client(neo4j_params) -> AASNeo4JClient:
    """A fresh client on the same DB (does NOT wipe), so its in-memory dedup map is empty."""
    return AASNeo4JClient(
        uri=neo4j_params["uri"],
        user=neo4j_params["user"],
        password=neo4j_params["password"],
        model_config=AAS_NEO4J_MODEL_CONFIG,
    )


def _count_reference(client, keys_value: list[str]) -> int:
    clause = "MATCH (r:Reference) WHERE r.keys_value = $kv RETURN count(r) AS c"
    with client.driver.session() as session:
        return session.run(clause, kv=keys_value).single()["c"]


def _count_incoming(client, keys_value: list[str], rel_type: str) -> int:
    clause = (
        f"MATCH (src)-[e:{rel_type}]->(r:Reference) WHERE r.keys_value = $kv RETURN count(e) AS c"
    )
    with client.driver.session() as session:
        return session.run(clause, kv=keys_value).single()["c"]


def test_reference_deduplicated_across_clients(aas_client, neo4j_params):
    # Client 1 imports SM1 referencing semantic id "0173-X".
    aas_client.add_identifiable(
        _submodel("urn:sm/1", "SM1", _property_with_semantic_id("P1", "0173-X"))
    )
    assert _count_reference(aas_client, ["0173-X"]) == 1

    # Client 2 (fresh, empty in-memory dedup) imports SM2 with the SAME semantic id.
    client2 = _second_client(neo4j_params)
    try:
        client2.add_identifiable(
            _submodel("urn:sm/2", "SM2", _property_with_semantic_id("P2", "0173-X"))
        )
    finally:
        client2.driver.close()

    # Still exactly one Reference node, now shared by both properties.
    assert _count_reference(aas_client, ["0173-X"]) == 1
    assert _count_incoming(aas_client, ["0173-X"], "semanticId") == 2


def test_hash_indexes_match_deduplicated_types(aas_client):
    """A hash index must exist for every configured deduplicated type."""
    clause = "SHOW INDEXES YIELD labelsOrTypes, properties RETURN labelsOrTypes AS labels, properties AS props"
    with aas_client.driver.session() as session:
        indexed_labels = {
            row["labels"][0]
            for row in session.run(clause)
            if row["props"] == ["hash"] and row["labels"]
        }
    for label in AAS_NEO4J_MODEL_CONFIG.deduplicated_object_types:
        assert label in indexed_labels, f"no hash index for deduplicated type {label}"


def test_distinct_references_not_merged(aas_client, neo4j_params):
    aas_client.add_identifiable(
        _submodel("urn:sm/1", "SM1", _property_with_semantic_id("P1", "0173-A"))
    )
    client2 = _second_client(neo4j_params)
    try:
        client2.add_identifiable(
            _submodel("urn:sm/2", "SM2", _property_with_semantic_id("P2", "0173-B"))
        )
    finally:
        client2.driver.close()

    assert _count_reference(aas_client, ["0173-A"]) == 1
    assert _count_reference(aas_client, ["0173-B"]) == 1
