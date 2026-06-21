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
from aas_mapping.mcp_server.abstract import build_abstract_submodel


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
        "validate_constraints",
        "list_submodel_types",
        "list_submodel_types_by_semantic_id",
        "abstract_submodel",
    }


# --- schema-validation path exercised through FastMCP (pydantic + guard) ---


def test_call_tool_rejects_missing_required_arg():
    # get_identifiable requires `identifier`; pydantic rejects the empty args.
    with pytest.raises(ToolError):
        anyio.run(mcp_server.mcp.call_tool, "get_identifiable", {})


def test_count_stats_uses_client():
    client = MagicMock()
    client.count_identifiables_by_type.return_value = {
        "assetAdministrationShells": 2,
        "submodels": 5,
        "conceptDescriptions": 9,
    }
    result = mcp_server.count_stats(_fake_ctx(client))
    assert result == {
        "assetAdministrationShells": 2,
        "submodels": 5,
        "conceptDescriptions": 9,
    }


def test_get_identifiable_not_found_raises_clean_error():
    client = MagicMock()
    client.get_identifiable.side_effect = KeyError("No Referable found with: id=missing")
    with pytest.raises(ValueError, match="No Identifiable found with id 'missing'"):
        mcp_server.get_identifiable("missing", _fake_ctx(client))


def test_get_referable_not_found_raises_clean_error():
    client = MagicMock()
    client.get_referable.side_effect = KeyError("No Referable found")
    with pytest.raises(ValueError, match=r"No Referable found at 'urn:sm -> Bad\.Path'"):
        mcp_server.get_referable("urn:sm", _fake_ctx(client), id_short_path="Bad.Path")


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


def test_list_submodel_types_returns_shape():
    client = MagicMock()
    client.execute_clause.return_value = [
        _mock_row({"idShort": "Nameplate", "semanticId": "urn:sem1", "count": 42}),
        _mock_row({"idShort": "ContactInformations", "semanticId": "urn:sem2", "count": 7}),
    ]
    result = mcp_server.list_submodel_types(_fake_ctx(client))
    assert result["total_types"] == 2
    assert result["types"][0] == {"idShort": "Nameplate", "semanticId": "urn:sem1", "count": 42}


def test_list_submodel_types_by_semantic_id_returns_shape():
    client = MagicMock()
    client.execute_clause.return_value = [
        _mock_row({"semanticId": "urn:sem1", "count": 49}),
        _mock_row({"semanticId": "urn:sem2", "count": 7}),
    ]
    result = mcp_server.list_submodel_types_by_semantic_id(_fake_ctx(client))
    assert result["total_types"] == 2
    assert result["types"][0] == {"semanticId": "urn:sem1", "count": 49}
    assert "idShort" not in result["types"][0]


def test_abstract_submodel_no_match_raises():
    client = MagicMock()
    client.get_submodels_by_type.return_value = []
    with pytest.raises(ValueError, match="No Submodels found"):
        mcp_server.abstract_submodel("NonExistent", _fake_ctx(client))


def test_abstract_submodel_strips_values():
    client = MagicMock()
    client.get_submodels_by_type.return_value = [
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
    client.get_submodels_by_type.return_value = [
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


def _sm(idshort, elements):
    return {"modelType": "Submodel", "id": f"urn:{idshort}", "idShort": idshort,
            "kind": "Instance", "submodelElements": elements}


def test_abstract_merges_nested_entity_statements():
    """Structure under an Entity (statements) must be merged across instances."""
    a = _sm("Asset", [
        {"modelType": "Entity", "idShort": "Motor", "entityType": "SelfManagedEntity",
         "statements": [
             {"modelType": "Property", "idShort": "Power", "valueType": "xs:int", "value": "5"},
         ]},
    ])
    b = _sm("Asset", [
        {"modelType": "Entity", "idShort": "Motor", "entityType": "SelfManagedEntity",
         "statements": [
             {"modelType": "Property", "idShort": "Power", "valueType": "xs:int", "value": "7"},
             {"modelType": "Property", "idShort": "Voltage", "valueType": "xs:int", "value": "230"},
         ]},
    ])
    abstract = build_abstract_submodel([a, b])
    motor = abstract["submodelElements"][0]
    names = {s["idShort"] for s in motor["statements"]}
    assert names == {"Power", "Voltage"}
    # instance values stripped
    assert all("value" not in s for s in motor["statements"])


def test_abstract_smlist_representative_is_union_of_items():
    """A SubmodelElementList collapses to one representative child that unions the
    structure of every list item (within and across instances)."""
    a = _sm("Doc", [
        {"modelType": "SubmodelElementList", "idShort": "Docs", "typeValueListElement": "SubmodelElementCollection",
         "value": [
             {"modelType": "SubmodelElementCollection", "value": [
                 {"modelType": "Property", "idShort": "Title", "valueType": "xs:string", "value": "T1"},
             ]},
             {"modelType": "SubmodelElementCollection", "value": [
                 {"modelType": "Property", "idShort": "Author", "valueType": "xs:string", "value": "A1"},
             ]},
         ]},
    ])
    abstract = build_abstract_submodel([a])
    docs = abstract["submodelElements"][0]
    assert len(docs["value"]) == 1  # single representative
    rep_children = {c["idShort"] for c in docs["value"][0]["value"]}
    assert rep_children == {"Title", "Author"}  # union of both list items


def test_abstract_submodel_yaml_format():
    client = MagicMock()
    client.get_submodels_by_type.return_value = [
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


def test_abstract_submodel_tries_semantic_id_then_idshort():
    """semanticId is tried first (the real type); idShort is the fallback."""
    from unittest.mock import call

    client = MagicMock()
    client.get_submodels_by_type.return_value = []  # both lookups miss
    with pytest.raises(ValueError, match="No Submodels found"):
        mcp_server.abstract_submodel("Doc", _fake_ctx(client))
    assert client.get_submodels_by_type.call_args_list == [
        call("Doc", by_semantic_id=True),
        call("Doc", by_semantic_id=False),
    ]


def test_abstract_submodel_semantic_id_match_skips_idshort_fallback():
    client = MagicMock()
    client.get_submodels_by_type.return_value = [
        {"modelType": "Submodel", "id": "urn:sm1", "idShort": "Doc",
         "kind": "Instance", "submodelElements": []}
    ]
    mcp_server.abstract_submodel("urn:type", _fake_ctx(client))
    # semanticId lookup hit -> idShort fallback not attempted
    client.get_submodels_by_type.assert_called_once_with("urn:type", by_semantic_id=True)


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
    assert result == {
        "assetAdministrationShells": 0,
        "submodels": 0,
        "conceptDescriptions": 0,
    }


@pytest.mark.integration
def test_count_stats_populated_integration(aas_client):
    import json as _json
    env_path = os.path.join(_EXAMPLE_SUBMODEL, "IDTA 02002-1-0_Template_ContactInformation.json")
    with open(env_path, encoding="utf-8") as f:
        env = _json.load(f)
    aas_client.upload_json(env)

    result = mcp_server.count_stats(_fake_ctx(aas_client))
    assert result["submodels"] == len(env.get("submodels", []))
    assert result["assetAdministrationShells"] == len(env.get("assetAdministrationShells", []))
    assert result["conceptDescriptions"] == len(env.get("conceptDescriptions", []))


@pytest.mark.integration
def test_list_submodel_types_by_semantic_id_integration(aas_client):
    import json as _json
    env_path = os.path.join(_EXAMPLE_SUBMODEL, "IDTA 02002-1-0_Template_ContactInformation.json")
    with open(env_path, encoding="utf-8") as f:
        env = _json.load(f)
    aas_client.upload_json(env)

    result = mcp_server.list_submodel_types_by_semantic_id(_fake_ctx(aas_client))
    total = sum(t["count"] for t in result["types"])
    assert total == len(env.get("submodels", []))
    assert all(set(t) == {"semanticId", "count"} for t in result["types"])


@pytest.mark.integration
def test_validate_constraints_empty_graph(aas_client):
    result = mcp_server.validate_constraints(_fake_ctx(aas_client))
    assert result["compliant"] is True
    assert result["violations"] == []
    assert len(result["checked_constraints"]) > 0


@pytest.mark.integration
def test_get_identifiable_missing_integration(aas_client):
    with pytest.raises(ValueError, match="No Identifiable found"):
        mcp_server.get_identifiable("urn:does-not-exist", _fake_ctx(aas_client))


@pytest.mark.integration
def test_abstract_submodel_no_match_integration(aas_client):
    with pytest.raises(ValueError, match="No Submodels found"):
        mcp_server.abstract_submodel("NonExistentType", _fake_ctx(aas_client))
