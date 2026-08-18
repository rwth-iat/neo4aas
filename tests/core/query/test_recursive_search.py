"""Integration test for no-path recursive $sme search.

Per the spec, `$sme#<attr>` with no idShort path searches all SubmodelElements at any
depth (variable-length traversal over the containment edges).

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher

pytestmark = pytest.mark.integration


def _nested_submodel() -> dict:
    return {
        "modelType": "Submodel",
        "id": "urn:sm/nested",
        "idShort": "SMnested",
        "submodelElements": [
            {"modelType": "Property", "idShort": "Top", "valueType": "xs:string", "value": "x"},
            {
                "modelType": "SubmodelElementCollection",
                "idShort": "Specs",
                "value": [
                    {
                        "modelType": "SubmodelElementCollection",
                        "idShort": "Inner",
                        "value": [
                            {"modelType": "Property", "idShort": "Color", "valueType": "xs:string", "value": "red"}
                        ],
                    }
                ],
            },
        ],
    }


def _run_eq(client, value: str) -> list:
    cond = {"$condition": {"$eq": [{"$field": "$sme#value"}, {"$strVal": value}]}}
    records = client.execute_clause(convert_aasql_to_cypher(cond)) or []
    return [r["sm"]["id"] for r in records if "sm" in r.keys()]


@pytest.fixture
def loaded(aas_client):
    aas_client.add_identifiable(_nested_submodel())
    return aas_client


def test_recursive_matches_depth_1(loaded):
    assert _run_eq(loaded, "x") == ["urn:sm/nested"]  # Top, direct child


def test_recursive_matches_deeply_nested(loaded):
    # Color is at depth 3 (submodelElements -> value -> value); recursive search must find it.
    assert _run_eq(loaded, "red") == ["urn:sm/nested"]


def test_recursive_no_false_match(loaded):
    assert _run_eq(loaded, "green") == []
