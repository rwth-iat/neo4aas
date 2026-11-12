"""optimize_database() derives the Identifiable `id` schema rule from the model config."""
import dataclasses

from neo4aas.core.base import BaseNeo4JClient
from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG


def _clauses(config) -> list[str]:
    client = BaseNeo4JClient(uri="", user="", model_config=config)
    executed = []
    client.execute_clause = lambda clause, single=False, params=None: executed.append(clause)
    client.optimize_database()
    return [c for c in executed if "(n:Identifiable) " in c and "n.id" in c]


def test_uniqueness_constraint_by_default():
    assert _clauses(AAS_NEO4J_MODEL_CONFIG) == [
        "CREATE CONSTRAINT identifiable_id IF NOT EXISTS FOR (n:Identifiable) REQUIRE n.id IS UNIQUE"
    ]


def test_plain_index_when_uniqueness_not_enforced():
    config = dataclasses.replace(AAS_NEO4J_MODEL_CONFIG, enforce_unique_ids=False)

    assert _clauses(config) == [
        "CREATE INDEX identifiable_id IF NOT EXISTS FOR (n:Identifiable) ON (n.id)"
    ]
