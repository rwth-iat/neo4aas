"""Integration test for cross-root AASQL queries ($aas filtered by $sme).

Per the IDTA query-language spec, combining ``$aas`` with ``$sme`` returns the AAS
whose (referenced) submodel matches the element condition. This exercises the full
path: import -> resolve_references() -> compile -> execute against Neo4j.

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher

pytestmark = pytest.mark.integration


def _submodel_with_color(sm_id: str, color: str) -> dict:
    return {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "SM1",
        "submodelElements": [
            {"modelType": "Property", "idShort": "Color", "valueType": "xs:string", "value": color}
        ],
    }


def _aas_referencing(aas_id: str, id_short: str, sm_id: str) -> dict:
    return {
        "modelType": "AssetAdministrationShell",
        "id": aas_id,
        "idShort": id_short,
        "assetInformation": {"assetKind": "Instance"},
        "submodels": [
            {"type": "ModelReference", "keys": [{"type": "Submodel", "value": sm_id}]}
        ],
    }


_QUERY = {
    "$condition": {
        "$and": [
            {"$eq": [{"$field": "$aas#idShort"}, {"$strVal": "RedShell"}]},
            {"$eq": [{"$field": "$sme.Color#value"}, {"$strVal": "red"}]},
        ]
    }
}


def _run(client):
    cypher = convert_aasql_to_cypher(_QUERY)
    records = client.execute_clause(cypher) or []
    return [r["aas"]["id"] for r in records]


def test_aas_returned_when_referenced_submodel_matches(aas_client):
    aas_client.add_identifiable(_submodel_with_color("urn:sm/red", "red"))
    aas_client.add_identifiable(_aas_referencing("urn:aas/red", "RedShell", "urn:sm/red"))
    aas_client.resolve_references()

    assert _run(aas_client) == ["urn:aas/red"]


def test_aas_not_returned_when_submodel_belongs_to_other_aas(aas_client):
    # The matching submodel exists but is referenced by a *different* AAS.
    # The :references join must keep the conditions scoped to RedShell's own submodels.
    aas_client.add_identifiable(_submodel_with_color("urn:sm/red", "red"))
    aas_client.add_identifiable(_submodel_with_color("urn:sm/blue", "blue"))
    aas_client.add_identifiable(_aas_referencing("urn:aas/red", "RedShell", "urn:sm/blue"))
    aas_client.add_identifiable(_aas_referencing("urn:aas/other", "OtherShell", "urn:sm/red"))
    aas_client.resolve_references()

    # RedShell references the blue submodel -> no match despite a red submodel existing.
    assert _run(aas_client) == []
