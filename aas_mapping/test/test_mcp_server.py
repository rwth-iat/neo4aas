"""Tests for the aas4graph MCP server.

Unit tests use a mocked client and need no Neo4j. Integration tests reuse the
`aas_client` fixture (skipped automatically when Neo4j is unreachable).
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from aas_mapping.mcp_server import server as mcp_server


def _fake_ctx(client):
    """Build a minimal Context stand-in exposing the lifespan client."""
    lifespan_context = SimpleNamespace(client=client)
    request_context = SimpleNamespace(lifespan_context=lifespan_context)
    return SimpleNamespace(request_context=request_context)


# ---------------------------------------------------------------------------
# Unit tests (no Neo4j)
# ---------------------------------------------------------------------------


def test_expected_tools_registered():
    tools = anyio.run(mcp_server.mcp.list_tools)
    names = {t.name for t in tools}
    assert names == {
        "count_stats",
        "get_identifiable",
        "get_referable",
        "compile_aasql",
        "query_aasql",
        "validate_constraints",
        "list_submodels",
        "list_submodel_types",
        "abstract_submodel",
    }


def test_compile_aasql_returns_cypher():
    query = {
        "$condition": {
            "$eq": [{"$field": "$aas#idShort"}, {"$strVal": "MyShell"}]
        }
    }
    cypher = mcp_server.compile_aasql(query)
    assert isinstance(cypher, str)
    assert "AssetAdministrationShell" in cypher
    assert "MyShell" in cypher


@pytest.mark.parametrize(
    "bad_query, expected",
    [
        ({}, r"\$condition"),
        ({"foo": "bar"}, r"\$condition"),
        ("not-a-dict", "JSON object"),
        (123, "JSON object"),
    ],
)
def test_compile_aasql_rejects_invalid_query(bad_query, expected):
    with pytest.raises(ValueError, match=expected):
        mcp_server.compile_aasql(bad_query)


# --- schema-validation path exercised through FastMCP (pydantic + guard) ---


def test_call_tool_rejects_missing_required_arg():
    # get_identifiable requires `identifier`; pydantic rejects the empty args.
    with pytest.raises(ToolError):
        anyio.run(mcp_server.mcp.call_tool, "get_identifiable", {})


def test_call_tool_rejects_wrong_arg_type():
    # `query` is typed dict[str, Any]; a string fails pydantic validation.
    with pytest.raises(ToolError):
        anyio.run(mcp_server.mcp.call_tool, "query_aasql", {"query": "not-a-dict"})


def test_call_tool_compile_aasql_missing_condition():
    # Passes pydantic (is a dict) but fails the structural guard.
    with pytest.raises(ToolError, match="condition"):
        anyio.run(mcp_server.mcp.call_tool, "compile_aasql", {"query": {"foo": "bar"}})


def test_count_stats_uses_client():
    client = MagicMock()
    client.count_identifiables.return_value = 3
    client.count_referables.return_value = 42
    result = mcp_server.count_stats(_fake_ctx(client))
    assert result == {"identifiables": 3, "referables": 42}


def test_query_aasql_strips_internal_keys():
    client = MagicMock()
    row = MagicMock()
    row.data.return_value = {"aas": {"idShort": "X", "uid": 1, "hash": "abc"}}
    client.execute_clause.return_value = [row]

    query = {
        "$condition": {
            "$eq": [{"$field": "$aas#idShort"}, {"$strVal": "X"}]
        }
    }
    result = mcp_server.query_aasql(query, _fake_ctx(client))

    assert result["count"] == 1
    assert result["results"] == [{"aas": {"idShort": "X"}}]
    assert "uid" not in result["results"][0]["aas"]
    client.execute_clause.assert_called_once_with(result["cypher"])


def test_get_referable_delegates():
    client = MagicMock()
    client.get_referable.return_value = {"idShort": "Color"}
    out = mcp_server.get_referable("some-id", _fake_ctx(client), id_short_path="Color")
    assert out == {"idShort": "Color"}
    client.get_referable.assert_called_once_with("some-id", "Color")


def _mock_row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def test_list_submodels_returns_shape():
    client = MagicMock()
    count_row = MagicMock()
    count_row.__getitem__ = lambda self, key: 2 if key == "total" else None
    client.execute_clause.side_effect = [
        count_row,  # COUNT query (single=True)
        [  # paginated rows
            _mock_row({"id": "urn:sm1", "idShort": "Nameplate", "kind": "Instance", "semanticId": "urn:sem1"}),
            _mock_row({"id": "urn:sm2", "idShort": "Nameplate", "kind": "Instance", "semanticId": "urn:sem1"}),
        ],
    ]
    result = mcp_server.list_submodels(_fake_ctx(client))
    assert result["total"] == 2
    assert result["submodels"][0]["id"] == "urn:sm1"
    assert result["submodels"][0]["idShort"] == "Nameplate"
    assert result["offset"] == 0
    assert result["limit"] == 100


def test_list_submodels_empty_graph():
    client = MagicMock()
    count_row = MagicMock()
    count_row.__getitem__ = lambda self, key: 0 if key == "total" else None
    client.execute_clause.side_effect = [count_row, []]
    result = mcp_server.list_submodels(_fake_ctx(client))
    assert result["total"] == 0
    assert result["submodels"] == []


def test_list_submodel_types_returns_shape():
    client = MagicMock()
    client.execute_clause.return_value = [
        _mock_row({"idShort": "Nameplate", "semanticId": "urn:sem1", "count": 42}),
        _mock_row({"idShort": "ContactInformations", "semanticId": "urn:sem2", "count": 7}),
    ]
    result = mcp_server.list_submodel_types(_fake_ctx(client))
    assert result["total_types"] == 2
    assert result["types"][0] == {"idShort": "Nameplate", "semanticId": "urn:sem1", "count": 42}


def test_abstract_submodel_no_match_raises():
    client = MagicMock()
    client.get_identifiables_by_type.return_value = []
    with pytest.raises(ValueError, match="No Submodels found"):
        mcp_server.abstract_submodel("NonExistent", _fake_ctx(client))


def test_abstract_submodel_strips_values():
    client = MagicMock()
    client.get_identifiables_by_type.return_value = [
        {
            "modelType": "Submodel",
            "id": "urn:sm1",
            "idShort": "Doc",
            "kind": "Instance",
            "submodelElements": [
                {"modelType": "Property", "idShort": "Title", "valueType": "xs:string", "value": "Hello"},
            ],
        }
    ]
    result = mcp_server.abstract_submodel("Doc", _fake_ctx(client))
    abstract = result["abstract_submodel"]
    assert abstract["kind"] == "Template"
    prop = abstract["submodelElements"][0]
    assert "value" not in prop
    assert prop["valueType"] == "xs:string"


def test_abstract_submodel_merges_structure():
    client = MagicMock()
    client.get_identifiables_by_type.return_value = [
        {
            "modelType": "Submodel",
            "id": "urn:sm1",
            "idShort": "Doc",
            "kind": "Instance",
            "submodelElements": [
                {"modelType": "Property", "idShort": "Title", "valueType": "xs:string", "value": "A"},
            ],
        },
        {
            "modelType": "Submodel",
            "id": "urn:sm2",
            "idShort": "Doc",
            "kind": "Instance",
            "submodelElements": [
                {"modelType": "Property", "idShort": "Title", "valueType": "xs:string", "value": "B"},
                {"modelType": "Property", "idShort": "Author", "valueType": "xs:string", "value": "X"},
            ],
        },
    ]
    result = mcp_server.abstract_submodel("Doc", _fake_ctx(client))
    abstract = result["abstract_submodel"]
    id_shorts = {e["idShort"] for e in abstract["submodelElements"]}
    assert id_shorts == {"Title", "Author"}
    assert result["instance_count"] == 2


def test_abstract_submodel_yaml_format():
    client = MagicMock()
    client.get_identifiables_by_type.return_value = [
        {
            "modelType": "Submodel",
            "id": "urn:sm1",
            "idShort": "Doc",
            "kind": "Instance",
            "submodelElements": [],
        }
    ]
    result = mcp_server.abstract_submodel("Doc", _fake_ctx(client), output_format="yaml")
    assert "yaml" in result
    assert "abstract_submodel" not in result
    assert "modelType: Submodel" in result["yaml"]


def test_abstract_submodel_uses_semantic_id_when_uri():
    client = MagicMock()
    client.get_identifiables_by_type.return_value = []
    with pytest.raises(ValueError):
        mcp_server.abstract_submodel("https://example.com/mytype", _fake_ctx(client))
    client.get_identifiables_by_type.assert_called_once_with(
        "https://example.com/mytype", by_semantic_id=True
    )


# ---------------------------------------------------------------------------
# Integration tests (require live Neo4j)
# ---------------------------------------------------------------------------

_EXAMPLE_SUBMODEL = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "examples",
    "submodels",
)


@pytest.mark.integration
def test_count_stats_integration(aas_client):
    result = mcp_server.count_stats(_fake_ctx(aas_client))
    assert result == {"identifiables": 0, "referables": 0}


@pytest.mark.integration
def test_validate_constraints_empty_graph(aas_client):
    result = mcp_server.validate_constraints(_fake_ctx(aas_client))
    assert result["compliant"] is True
    assert result["violations"] == []
    assert len(result["checked_constraints"]) > 0


@pytest.mark.integration
def test_list_submodels_integration(aas_client):
    result = mcp_server.list_submodels(_fake_ctx(aas_client))
    assert result["total"] == 0
    assert result["submodels"] == []


@pytest.mark.integration
def test_abstract_submodel_no_match_integration(aas_client):
    with pytest.raises(ValueError, match="No Submodels found"):
        mcp_server.abstract_submodel("NonExistentType", _fake_ctx(aas_client))
