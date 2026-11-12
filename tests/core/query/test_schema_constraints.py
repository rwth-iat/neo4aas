"""Integration tests for schema constraints created by optimize_database().

Requires a live Neo4j; skipped automatically when unreachable.
"""
import neo4j
import pytest

pytestmark = pytest.mark.integration


def _identifiable_id_constraints(client) -> list:
    clause = (
        "SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
        "RETURN type AS type, labelsOrTypes AS labels, properties AS props"
    )
    with client.driver.session() as session:
        return [
            (r["type"], r["labels"], r["props"])
            for r in session.run(clause)
            if r["labels"] == ["Identifiable"] and r["props"] == ["id"]
        ]


def test_identifiable_id_uniqueness_constraint_created(aas_client):
    aas_client.optimize_database()

    constraints = _identifiable_id_constraints(aas_client)
    assert constraints, "no Identifiable(id) constraint found"
    assert all(t.startswith("UNIQUE") for t, _, _ in constraints)


def test_duplicate_identifiable_id_rejected_at_db_level(aas_client):
    aas_client.optimize_database()

    aas_client.execute_clause("CREATE (:Identifiable:Submodel {id: 'urn:dup'})")
    with pytest.raises(neo4j.exceptions.ClientError):
        aas_client.execute_clause("CREATE (:Identifiable:Submodel {id: 'urn:dup'})")


def test_constraint_present_without_explicit_optimize(neo4j_params, neo4j_available_or_skip):
    """A freshly constructed client must already have the schema in place."""
    from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG

    client = AASNeo4JClient(
        uri=neo4j_params["uri"],
        user=neo4j_params["user"],
        password=neo4j_params["password"],
        model_config=AAS_NEO4J_MODEL_CONFIG,
    )
    try:
        # No optimize_database() call here — the constructor must have run it.
        assert _identifiable_id_constraints(client)
    finally:
        client.driver.close()


def test_optimize_database_is_idempotent(aas_client):
    # IF NOT EXISTS means a second run must not raise.
    aas_client.optimize_database()
    aas_client.optimize_database()

    assert _identifiable_id_constraints(aas_client)


def _identifiable_id_indexes(client) -> list:
    clause = (
        "SHOW INDEXES YIELD type, owningConstraint, labelsOrTypes, properties "
        "WHERE owningConstraint IS NULL "
        "RETURN type AS type, labelsOrTypes AS labels, properties AS props"
    )
    with client.driver.session() as session:
        return [
            r["type"]
            for r in session.run(clause)
            if r["labels"] == ["Identifiable"] and r["props"] == ["id"]
        ]


def _drop_identifiable_id_rule(client):
    # Constraint first: Neo4j refuses to drop an index that backs a constraint.
    client.execute_clause("DROP CONSTRAINT identifiable_id IF EXISTS")
    client.execute_clause("DROP INDEX identifiable_id IF EXISTS")


def test_index_instead_of_constraint_when_ids_not_enforced(neo4j_params, neo4j_available_or_skip):
    import dataclasses

    from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG

    client = AASNeo4JClient(
        uri=neo4j_params["uri"],
        user=neo4j_params["user"],
        password=neo4j_params["password"],
        model_config=dataclasses.replace(AAS_NEO4J_MODEL_CONFIG, enforce_unique_ids=False),
        auto_optimize=False,
    )
    try:
        client._remove_all()
        _drop_identifiable_id_rule(client)
        client.optimize_database()

        assert not _identifiable_id_constraints(client)
        assert _identifiable_id_indexes(client) == ["RANGE"]

        client.execute_clause("CREATE (:Identifiable:Submodel {id: 'urn:dup'})")
        client.execute_clause("CREATE (:Identifiable:Submodel {id: 'urn:dup'})")
        count = client.execute_clause(
            "MATCH (n:Identifiable {id: 'urn:dup'}) RETURN count(n) AS c", single=True
        )["c"]
        assert count == 2
    finally:
        # Leave the shared database as the other tests expect it: empty and without the
        # plain index, so the next client can create the uniqueness constraint.
        client._remove_all()
        _drop_identifiable_id_rule(client)
        client.driver.close()
