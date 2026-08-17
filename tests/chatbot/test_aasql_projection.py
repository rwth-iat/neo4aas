"""Unit tests for the aasql_query summary projection helpers (no repo / no LLM).

Regression for the looping case (chat 17df6cd2): full Submodel JSON over-filled the
observation budget, so only ~5 of 31 results showed and the agent retried. The compact
projection lets every identity row fit one observation.

Run (chatbot deps only, no Neo4j):

    KICONNECT_API_KEY=dummy uv run \
      --with langchain-openai --with-editable ../.. \
      python -m pytest test_aasql_projection.py -v
"""
import csv
import io
import os

os.environ.setdefault("KICONNECT_API_KEY", "dummy")  # config import requires it; LLM unused

from aas_mapping.chatbot_v2.tools import _mlp_text, _project_elements, _project_row, _rows_to_csv, _sem_id


def test_sem_id_single_and_multi_key():
    assert _sem_id({"semanticId": {"keys": [{"value": "0173-1#01-AAA"}]}}) == "0173-1#01-AAA"
    multi = {"semanticId": {"keys": [{"value": "A"}, {"value": "B"}]}}
    assert _sem_id(multi) == "A|B"
    assert _sem_id({}) == ""


def test_mlp_text_language_preference():
    val = [{"language": "zh", "text": "泵"}, {"language": "de", "text": "Pumpe"},
           {"language": "en", "text": "Pump"}]
    assert _mlp_text(val) == "Pump"            # en wins over de/zh regardless of order
    assert _mlp_text([{"language": "de", "text": "Pumpe"}]) == "Pumpe"  # de fallback
    assert _mlp_text("scalar") == "scalar"     # plain scalar passes through


def test_project_row_submodel_vs_shell():
    sm = {"modelType": "Submodel", "idShort": "OperationalData", "id": "urn:x",
          "semanticId": {"keys": [{"value": "sid"}]}}
    assert _project_row(sm, "submodels") == {
        "modelType": "Submodel", "idShort": "OperationalData", "id": "urn:x",
        "semanticId": "sid"}
    shell = {"modelType": "AssetAdministrationShell", "idShort": "F17", "id": "urn:s",
             "assetInformation": {"globalAssetId": "urn:asset"}}
    assert _project_row(shell, "shells") == {
        "modelType": "AssetAdministrationShell", "idShort": "F17", "id": "urn:s",
        "globalAssetId": "urn:asset"}


def test_project_elements_compact():
    obj = {"submodelElements": [
        {"idShort": "CurrentValue", "modelType": "Property", "value": 139.28},
        {"idShort": "Name", "modelType": "MultiLanguageProperty",
         "value": [{"language": "de", "text": "Name"}, {"language": "en", "text": "Nm"}]},
        {"idShort": "Group", "modelType": "SubmodelElementCollection", "value": []},
    ]}
    assert _project_elements(obj) == [
        {"idShort": "CurrentValue", "modelType": "Property", "value": 139.28},
        {"idShort": "Name", "modelType": "MultiLanguageProperty", "value": "Nm"},
        {"idShort": "Group", "modelType": "SubmodelElementCollection", "value": None},
    ]


def test_rows_to_csv_header_and_quoting():
    rows = [{"modelType": "Submodel", "idShort": "X", "id": "urn:1", "semanticId": "A|B"}]
    out = _rows_to_csv(rows)
    parsed = list(csv.DictReader(io.StringIO(out)))
    assert parsed[0]["semanticId"] == "A|B"      # multi-key cell survives round-trip
    assert out.splitlines()[0] == "modelType,idShort,id,semanticId"
    assert _rows_to_csv([]) == ""
