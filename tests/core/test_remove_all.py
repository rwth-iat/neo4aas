"""Integration tests: `_remove_all` wipes the graph in bounded batches.

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

pytestmark = pytest.mark.integration


def _count(client, clause: str) -> int:
    with client.driver.session() as session:
        return session.run(clause).single()["c"]


@pytest.mark.parametrize("batch_size", [7, 50, 10000])
def test_remove_all_wipes_dense_hub(aas_client, batch_size):
    # One hub with many incoming relationships, like a shared semanticId Reference:
    # its relationships outnumber the batch size, so they must not all go in one batch.
    with aas_client.driver.session() as session:
        session.run(
            "CREATE (hub:Reference) "
            "WITH hub UNWIND range(1, 100) AS i "
            "CREATE (:Property {i: i})-[:semanticId]->(hub)"
        )
    assert _count(aas_client, "MATCH ()-[r]->() RETURN count(r) AS c") == 100

    aas_client._remove_all(batch_size=batch_size)

    assert _count(aas_client, "MATCH (n) RETURN count(n) AS c") == 0
    assert _count(aas_client, "MATCH ()-[r]->() RETURN count(r) AS c") == 0


def test_remove_all_on_empty_graph(aas_client):
    aas_client._remove_all(batch_size=10)
    assert _count(aas_client, "MATCH (n) RETURN count(n) AS c") == 0
