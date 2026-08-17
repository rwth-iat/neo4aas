"""Integration tests: the import-internal `uid` property must not persist on nodes.

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

pytestmark = pytest.mark.integration


def _submodel_with_reference() -> dict:
    return {
        "modelType": "Submodel",
        "id": "urn:sm/1",
        "idShort": "SM1",
        "submodelElements": [
            {
                "modelType": "SubmodelElementCollection",
                "idShort": "Specs",
                "value": [
                    {
                        "modelType": "Property",
                        "idShort": "Color",
                        "valueType": "xs:string",
                        "value": "red",
                        "semanticId": {
                            "type": "ExternalReference",
                            "keys": [{"type": "GlobalReference", "value": "0173-X"}],
                        },
                    }
                ],
            }
        ],
    }


def _count(client, clause: str) -> int:
    with client.driver.session() as session:
        return session.run(clause).single()["c"]


def test_uid_removed_from_all_nodes_after_import(aas_client):
    aas_client.add_identifiable(_submodel_with_reference())

    assert _count(aas_client, "MATCH (n) WHERE n.uid IS NOT NULL RETURN count(n) AS c") == 0
    # Sanity: nodes were actually created.
    assert _count(aas_client, "MATCH (n) RETURN count(n) AS c") > 0


def test_hash_preserved_after_uid_cleanup(aas_client):
    # uid cleanup must not strip the hash that deduplication relies on.
    aas_client.add_identifiable(_submodel_with_reference())

    assert _count(aas_client, "MATCH (r:Reference) WHERE r.hash IS NOT NULL RETURN count(r) AS c") > 0
