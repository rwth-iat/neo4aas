"""Regression tests for the bugs documented in Report2.md.

These are written TDD-style: each one demonstrates a defect in the current code and
encodes the expected correct behaviour, so they fail until the corresponding bug is
fixed.

- C1: stale dedup/uid caches survive deletes -> commit()/re-add drops Reference edges (integration)
- C2: AASQL compiler injects unescaped string literals / idShorts (unit, no Neo4j)
- C3: _create_relationships swallows TransientError -> silent partial data loss (unit, no Neo4j)
- C4: add_submodel_element drops list_index/is_list -> list member exported as scalar (integration)
- C5: remove_referable deletes multiple subtrees on a non-unique idShort path (integration)
"""
import pytest
from neo4j.exceptions import TransientError

from aas_mapping.aas_neo4j_adapter.jsonification.neo4j_import import JsonToNeo4jImporter
from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import convert_aasql_to_cypher


# --------------------------------------------------------------------------- helpers

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


def _submodel_with_prop(sm_id: str, id_short: str, prop: dict) -> dict:
    return {"modelType": "Submodel", "id": sm_id, "idShort": id_short, "submodelElements": [prop]}


def _semantic_id_edge_count(client, sm_id: str) -> int:
    clause = (
        "MATCH (:Submodel {id: $sm})-[:submodelElements]->(:Property)"
        "-[:semanticId]->(:Reference) RETURN count(*) AS c"
    )
    with client.driver.session() as session:
        return session.run(clause, sm=sm_id).single()["c"]


# --------------------------------------------------------------------------- C2 (unit)

def _quote_is_escaped(cypher: str, raw: str) -> bool:
    """True if `raw` (containing a single quote) appears in a Cypher-safe escaped form.

    Valid single-quote escapes inside a single-quoted Cypher string literal are ``\\'``
    or ``''``. The raw, unescaped form (e.g. ``'O'Brien'``) is an injection / syntax bug.
    """
    escaped_backslash = raw.replace("'", "\\'")
    escaped_double = raw.replace("'", "''")
    return escaped_backslash in cypher or escaped_double in cypher


def test_c2_string_literal_with_quote_is_escaped():
    """A $strVal containing a single quote must be emitted escaped, not raw."""
    query = {
        "$condition": {"$eq": [{"$field": "$sm#idShort"}, {"$strVal": "O'Brien"}]}
    }
    cypher = convert_aasql_to_cypher(query)
    assert _quote_is_escaped(cypher, "O'Brien"), (
        f"unescaped single quote injected into Cypher: {cypher!r}"
    )


def test_c2_idshort_path_with_quote_is_escaped():
    """An idShort path segment containing a single quote must be emitted escaped."""
    query = {
        "$condition": {"$eq": [{"$field": "$sme.Va'lue#idShort"}, {"$strVal": "x"}]}
    }
    cypher = convert_aasql_to_cypher(query)
    assert _quote_is_escaped(cypher, "Va'lue"), (
        f"unescaped single quote injected into Cypher idShort: {cypher!r}"
    )


# --------------------------------------------------------------------------- C3 (unit)

class _RaisingSession:
    """Stub Neo4j session whose `run` always fails with a (retryable) TransientError."""

    def run(self, *args, **kwargs):
        raise TransientError("simulated deadlock")


def test_c3_transient_error_not_swallowed():
    """A TransientError during relationship creation must not be silently swallowed.

    The current code logs and continues, reporting success while losing the whole
    batch. Correct behaviour is to retry or propagate — never to drop edges silently.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")  # driver=None, no Neo4j needed
    relationships = {"value": [{"from_uid": 1, "to_uid": 2, "rel_props": {}}]}
    uid_to_internal_id = {1: "e1", 2: "e2"}

    with pytest.raises(TransientError):
        importer._create_relationships(_RaisingSession(), relationships, uid_to_internal_id)


# --------------------------------------------------------------------------- C1 (integration)

@pytest.mark.integration
def test_c1_readd_after_delete_keeps_reference_edges(aas_client):
    """Re-adding an Identifiable on the same client after deleting it must not drop
    its deduplicated Reference (semanticId) edges.

    The instance-scoped dedup / uid caches still map the Reference hash to the now-deleted
    node, so on re-add the Reference is not recreated and its edge dangles.
    """
    sm_id = "urn:sm/c1"
    sm = _submodel_with_prop(sm_id, "SM1", _property_with_semantic_id("P1", "0173-C1"))

    aas_client.add_identifiable(sm)
    assert _semantic_id_edge_count(aas_client, sm_id) == 1

    # Simulate Neo4jObjectStore.commit(): delete then re-add on the SAME client.
    aas_client.remove_identifiable(sm_id)
    aas_client.add_identifiable(sm)

    assert _semantic_id_edge_count(aas_client, sm_id) == 1, "semanticId edge lost after re-add"

    data = aas_client.get_identifiable(sm_id)
    prop = data["submodelElements"][0]
    assert "semanticId" in prop, "semanticId missing from re-added property"


# --------------------------------------------------------------------------- C4 (integration)

@pytest.mark.integration
def test_c4_add_element_into_list_stays_a_list_member(aas_client):
    """An element added into a SubmodelElementList via add_referable must round-trip as
    an ordered list member, not collapse the list to a scalar.

    The hand-built `value` edge lacks is_list/list_index, so export treats it as a scalar
    and overwrites the whole list.
    """
    sm_id = "urn:sm/c4"
    sm = {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "SM1",
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
            }
        ],
    }
    aas_client.add_identifiable(sm)

    new_prop = {"modelType": "Property", "valueType": "xs:string", "value": "c"}
    aas_client.add_referable(new_prop, parent_id=sm_id, id_short_path="Items")

    data = aas_client.get_identifiable(sm_id)
    items = next(e for e in data["submodelElements"] if e["idShort"] == "Items")
    value = items["value"]
    assert isinstance(value, list), f"list collapsed to scalar: {value!r}"
    assert len(value) == 3, f"expected 3 list members, got {len(value)}"
    assert {v["value"] for v in value} == {"a", "b", "c"}


# --------------------------------------------------------------------------- C5 (integration)

@pytest.mark.integration
def test_c5_remove_referable_refuses_non_unique_path(aas_client):
    """remove_referable must not silently delete multiple subtrees when an idShort path
    matches more than one node.

    A spec-violating duplicate idShort under one collection should be rejected (as
    _find_node does), not result in an unbounded DETACH DELETE.
    """
    sm_id = "urn:sm/c5"
    sm = {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "SM1",
        "submodelElements": [
            {
                "modelType": "SubmodelElementCollection",
                "idShort": "Coll",
                "value": [
                    {"modelType": "Property", "idShort": "Dup", "valueType": "xs:string", "value": "1"},
                    {"modelType": "Property", "idShort": "Dup", "valueType": "xs:string", "value": "2"},
                ],
            }
        ],
    }
    aas_client.add_identifiable(sm)

    with pytest.raises(ValueError):
        aas_client.remove_referable(sm_id, "Coll.Dup")
