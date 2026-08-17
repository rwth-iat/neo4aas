"""Integration tests for querying MultiLanguageProperty (and Property) #value / #language.

A Property stores its text in a scalar `value`; a MultiLanguageProperty stores text in
`value_text[]` and language codes in `value_language[]`. The compiler maps `#value` to
``coalesce(value_text, [value])`` so a single query works for both element types, and wraps
the comparison in ``any(...)`` so every operator (eq, contains, starts-with, regex, …) works.

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import convert_aasql_to_cypher

pytestmark = pytest.mark.integration


def _submodel() -> dict:
    return {
        "modelType": "Submodel",
        "id": "urn:sm/mlp",
        "idShort": "SMmlp",
        "submodelElements": [
            {
                "modelType": "MultiLanguageProperty",
                "idShort": "Note",
                "value": [
                    {"language": "en", "text": "Hello"},
                    {"language": "nl", "text": "Hallo"},
                ],
            },
            {"modelType": "Property", "idShort": "Color", "valueType": "xs:string", "value": "red"},
        ],
    }


def _run(client, condition: dict) -> list:
    cypher = convert_aasql_to_cypher({"$condition": condition})
    records = client.execute_clause(cypher) or []
    return [r["sm"]["id"] for r in records if "sm" in r.keys()]


def _eq(field, value):
    return {"$eq": [{"$field": field}, {"$strVal": value}]}


@pytest.fixture
def loaded(aas_client):
    aas_client.add_identifiable(_submodel())
    return aas_client


def test_mlp_value_eq_matches_any_language(loaded):
    assert _run(loaded, _eq("$sme.Note#value", "Hallo")) == ["urn:sm/mlp"]
    assert _run(loaded, _eq("$sme.Note#value", "Hello")) == ["urn:sm/mlp"]


def test_mlp_value_no_match(loaded):
    assert _run(loaded, _eq("$sme.Note#value", "Bonjour")) == []


def test_mlp_value_starts_with(loaded):
    cond = {"$starts-with": [{"$field": "$sme.Note#value"}, {"$strVal": "Hal"}]}
    assert _run(loaded, cond) == ["urn:sm/mlp"]


def test_mlp_value_contains(loaded):
    cond = {"$contains": [{"$field": "$sme.Note#value"}, {"$strVal": "ell"}]}
    assert _run(loaded, cond) == ["urn:sm/mlp"]  # "Hello"


def test_mlp_language_eq(loaded):
    assert _run(loaded, _eq("$sme.Note#language", "nl")) == ["urn:sm/mlp"]
    assert _run(loaded, _eq("$sme.Note#language", "fr")) == []


def test_plain_property_value_still_works(loaded):
    # The coalesce form must not break scalar Property #value.
    assert _run(loaded, _eq("$sme.Color#value", "red")) == ["urn:sm/mlp"]
    assert _run(loaded, _eq("$sme.Color#value", "blue")) == []
