"""Shared agent_tools — the single source of truth behind the MCP server and chatbot."""

from unittest.mock import MagicMock

import pytest

from aas_mapping.aas_neo4j_adapter import agent_tools


# ---------------------------------------------------------------------------
# Unit (no Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) DETACH DELETE n",
        "CREATE (n:X)",
        "MATCH (n) SET n.x = 1",
        "MERGE (n:Y {id: 'z'})",
        "MATCH (n) REMOVE n.x",
    ],
)
def test_cypher_read_rejects_writes(cypher):
    with pytest.raises(ValueError, match="read-only"):
        agent_tools.cypher_read(MagicMock(), cypher)


def test_cypher_read_rejects_empty():
    with pytest.raises(ValueError, match="Empty"):
        agent_tools.cypher_read(MagicMock(), "   ")


def test_get_identifiable_maps_keyerror():
    client = MagicMock()
    client.get_identifiable.side_effect = KeyError("nope")
    with pytest.raises(ValueError, match="No Identifiable found with id 'x'"):
        agent_tools.get_identifiable(client, "x")


# ---------------------------------------------------------------------------
# Integration (live Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cypher_read_executes(aas_client):
    out = agent_tools.cypher_read(aas_client, "RETURN 1 AS one")
    assert out == {"count": 1, "rows": [{"one": 1}]}


@pytest.mark.integration
def test_cypher_read_blocks_write_at_db(aas_client):
    # Even if the denylist were bypassed, the READ transaction must reject writes.
    with pytest.raises(ValueError):
        agent_tools.cypher_read(aas_client, "CREATE (n:ShouldNotExist) RETURN n")
    rows = aas_client.execute_clause("MATCH (n:ShouldNotExist) RETURN count(n) AS c")
    assert rows[0]["c"] == 0


@pytest.mark.integration
def test_count_stats_and_types_on_empty_graph(aas_client):
    assert agent_tools.count_stats(aas_client) == {
        "assetAdministrationShells": 0, "submodels": 0, "conceptDescriptions": 0,
    }
    assert agent_tools.list_submodel_types(aas_client) == {"total_types": 0, "types": []}
    assert agent_tools.list_submodel_types_by_semantic_id(aas_client) == {
        "total_types": 0, "types": [],
    }
