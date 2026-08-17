"""Unit tests for JsonToNeo4jImporter internals (no live Neo4j required)."""
import pytest
from neo4j.exceptions import TransientError

from aas_mapping.aas_neo4j_adapter.jsonification.neo4j_import import JsonToNeo4jImporter


class _RaisingSession:
    """Stub Neo4j session whose `run` always fails with a (retryable) TransientError."""

    def run(self, *args, **kwargs):
        raise TransientError("simulated deadlock")


def test_transient_error_during_relationship_creation_is_not_swallowed():
    """A TransientError while creating relationships must propagate, not be swallowed.

    Swallowing it would drop the whole batch of edges while reporting success, silently
    corrupting the graph. Correct behaviour is to surface the failure to the caller.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")  # driver is None; no Neo4j needed
    relationships = {"value": [{"from_uid": 1, "to_uid": 2, "rel_props": {}}]}
    uid_to_internal_id = {1: "e1", 2: "e2"}

    with pytest.raises(TransientError):
        importer._create_relationships(_RaisingSession(), relationships, uid_to_internal_id)


def test_group_nodes_by_label_does_not_mutate_input():
    """Grouping must not strip `labels` from the caller's node dicts.

    `labels` becomes Neo4j node labels, not properties, so it is excluded from each
    grouped bucket — but via a copy, leaving the input reusable. Previously a `pop`
    deleted `labels` in place, so a second grouping pass raised KeyError.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")
    nodes = [
        {"labels": ["Property", "SubmodelElement"], "uid": 1, "idShort": "a"},
        {"labels": ["Submodel"], "uid": 2, "id": "urn:sm"},
    ]

    grouped = importer._group_nodes_by_label(nodes)

    # Input untouched: labels still present, dicts reusable.
    assert nodes[0]["labels"] == ["Property", "SubmodelElement"]
    assert nodes[1]["labels"] == ["Submodel"]
    # Buckets keyed by sorted label tuple, with labels excluded from the payload.
    assert grouped[("Property", "SubmodelElement")] == [{"uid": 1, "idShort": "a"}]
    assert grouped[("Submodel",)] == [{"uid": 2, "id": "urn:sm"}]
    # Idempotent: a second pass works (no KeyError) and yields the same result.
    assert importer._group_nodes_by_label(nodes) == grouped
