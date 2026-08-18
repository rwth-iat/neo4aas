"""Import-time AAS fixers.

Unit tests for the fixer framework + LangStringFixer (no Neo4j), plus an
integration test that the language-tag fix is applied on the real import path.
"""

import pytest

from neo4aas.core.fixers import (
    LangStringFixer,
    NumericValueTypeFixer,
    IdShortFixer,
    EmptyLangStringFixer,
    apply_fixers,
    DEFAULT_FIXERS,
)


# ---------------------------------------------------------------------------
# Unit tests (no Neo4j)
# ---------------------------------------------------------------------------


def test_langstringfixer_normalizes_nested_tags():
    env = {
        "submodels": [
            {
                "modelType": "Submodel",
                "id": "urn:sm",
                "description": [{"language": "en_US", "text": "Hi"}],
                "submodelElements": [
                    {
                        "modelType": "MultiLanguageProperty",
                        "idShort": "Name",
                        "value": [
                            {"language": "de_DE", "text": "Hallo"},
                            {"language": "en", "text": "Hello"},  # already valid
                        ],
                    }
                ],
            }
        ]
    }
    n = LangStringFixer().fix(env)
    assert n == 2
    assert env["submodels"][0]["description"][0]["language"] == "en-US"
    mlp = env["submodels"][0]["submodelElements"][0]
    assert mlp["value"][0]["language"] == "de-DE"
    assert mlp["value"][1]["language"] == "en"  # untouched


def test_langstringfixer_noop_when_all_valid():
    data = {"value": [{"language": "en-GB", "text": "x"}]}
    assert LangStringFixer().fix(data) == 0
    assert data["value"][0]["language"] == "en-GB"


def test_apply_fixers_reports_counts():
    data = {"description": [{"language": "fr_FR", "text": "Bonjour"}]}
    report = apply_fixers(data)
    assert report.total == 1
    assert report.counts["language-tag-bcp47"] == 1
    assert data["description"][0]["language"] == "fr-FR"


def test_default_fixers_includes_language_fixer():
    assert any(isinstance(f, LangStringFixer) for f in DEFAULT_FIXERS)


def test_numeric_value_type_fixer_relaxes_non_numeric_string():
    # A float Property whose value is a stringified range (Phoenix Contact shape).
    data = {
        "submodelElements": [
            {
                "modelType": "Property",
                "idShort": "CrossSection",
                "valueType": "xs:float",
                "value": '[{"level":"max","value":0.2},{"level":"min","value":1.5}]',
            },
            {"modelType": "Property", "valueType": "xs:float", "value": "0.2"},  # valid
            {"modelType": "Property", "valueType": "xs:int", "value": "12"},  # valid
            {"modelType": "Property", "valueType": "xs:string", "value": "[{...}]"},  # not numeric
        ]
    }
    n = NumericValueTypeFixer().fix(data)
    assert n == 1
    smes = data["submodelElements"]
    assert smes[0]["valueType"] == "xs:string"  # coerced
    assert smes[0]["value"] == '[{"level":"max","value":0.2},{"level":"min","value":1.5}]'  # preserved
    assert smes[1]["valueType"] == "xs:float"  # untouched, parses
    assert smes[2]["valueType"] == "xs:int"  # untouched, parses
    assert smes[3]["valueType"] == "xs:string"  # untouched, already string


def test_numeric_value_type_fixer_in_default_fixers():
    assert any(isinstance(f, NumericValueTypeFixer) for f in DEFAULT_FIXERS)


def test_idshort_fixer_sanitizes_illegal_chars():
    data = {
        "assetAdministrationShells": [
            {"modelType": "AssetAdministrationShell", "idShort": "ABB_PMGA11* Control Unit_3103"},
        ],
        "submodels": [
            {"modelType": "Submodel", "idShort": "Already_Valid_1"},  # untouched
            {"modelType": "Submodel"},  # no idShort — skipped
        ],
    }
    n = IdShortFixer().fix(data)
    assert n == 1
    assert data["assetAdministrationShells"][0]["idShort"] == "ABB_PMGA11__Control_Unit_3103"
    assert data["submodels"][0]["idShort"] == "Already_Valid_1"  # legal chars kept


def test_idshort_fixer_replaces_hyphen_by_default():
    """AAS V3.1+ permits a hyphen in an idShort, but basyx implements V3.0 and rejects it
    ("must contain only letters, digits and underscore") — which is exactly the read-back
    failure this fixer exists to prevent. Default target is therefore the V3.0 charset."""
    data = {"submodels": [{"modelType": "Submodel", "idShort": "Max-Flow-Rate"}]}
    assert IdShortFixer().fix(data) == 1
    assert data["submodels"][0]["idShort"] == "Max_Flow_Rate"


def test_idshort_fixer_can_keep_hyphens_for_v31_consumers():
    data = {"submodels": [
        {"modelType": "Submodel", "idShort": "Max-Flow-Rate"},   # valid V3.1 -> untouched
        {"modelType": "Submodel", "idShort": "trailing-"},        # trailing '-' is invalid
        {"modelType": "Submodel", "idShort": "with space"},
    ]}
    assert IdShortFixer(allow_hyphen=True).fix(data) == 2
    assert [sm["idShort"] for sm in data["submodels"]] == [
        "Max-Flow-Rate", "trailing_", "with_space",
    ]


@pytest.mark.parametrize(
    "raw, fixed",
    [
        ("3PhaseMotor", "x3PhaseMotor"),
        ("_hidden", "x_hidden"),
        ("*abc", "x_abc"),
    ],
)
def test_idshort_fixer_enforces_leading_letter(raw, fixed):
    """AASd-002 also requires the first character to be a letter."""
    data = {"submodels": [{"modelType": "Submodel", "idShort": raw}]}
    assert IdShortFixer().fix(data) == 1
    assert data["submodels"][0]["idShort"] == fixed


def test_idshort_fixer_output_is_accepted_by_basyx():
    """End-to-end guard: whatever the fixer emits must pass basyx's AASd-002 check."""
    basyx_model = pytest.importorskip("basyx.aas.model")
    data = {
        "submodels": [
            {"modelType": "Submodel", "idShort": "ABB_PMGA11* Control Unit_3103"},
            {"modelType": "Submodel", "idShort": "Max-Flow-Rate"},
            {"modelType": "Submodel", "idShort": "3PhaseMotor"},
        ]
    }
    IdShortFixer().fix(data)
    for sm in data["submodels"]:
        basyx_model.Referable.validate_id_short(sm["idShort"])


def test_idshort_fixer_in_default_fixers():
    assert any(isinstance(f, IdShortFixer) for f in DEFAULT_FIXERS)


def test_empty_langstring_fixer_drops_empty_text():
    data = {
        "submodels": [
            {
                "modelType": "Submodel",
                "description": [
                    {"language": "en", "text": ""},      # dropped
                    {"language": "de", "text": "Hallo"},  # kept
                ],
                "submodelElements": [
                    {
                        "modelType": "MultiLanguageProperty",
                        "idShort": "Name",
                        "value": [{"language": "en", "text": "x"}],  # kept
                    },
                    {
                        "modelType": "MultiLanguageProperty",
                        "idShort": "Empty",
                        "value": [{"language": "en"}],  # missing text → dropped, list left []
                    },
                ],
            }
        ]
    }
    n = EmptyLangStringFixer().fix(data)
    assert n == 2
    sm = data["submodels"][0]
    assert sm["description"] == [{"language": "de", "text": "Hallo"}]
    assert sm["submodelElements"][0]["value"] == [{"language": "en", "text": "x"}]
    assert sm["submodelElements"][1]["value"] == []  # emptied but valid


def test_empty_langstring_fixer_in_default_fixers():
    assert any(isinstance(f, EmptyLangStringFixer) for f in DEFAULT_FIXERS)


def test_client_accepts_fix_on_import_kwarg():
    """fix_on_import must be accepted through the full inheritance chain (no Neo4j)."""
    from neo4aas.core.client import (
        AASNeo4JClient,
        AAS_NEO4J_MODEL_CONFIG,
    )

    c = AASNeo4JClient(uri=None, user="x", password="y",
                       model_config=AAS_NEO4J_MODEL_CONFIG, fix_on_import=True)
    assert c.fix_on_import is True
    assert AASNeo4JClient(uri=None, user="x").fix_on_import is False


# ---------------------------------------------------------------------------
# Integration test (requires live Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_language_tag_fixed_on_import(aas_client):
    aas_client.fix_on_import = True  # opt in
    env = {
        "assetAdministrationShells": [],
        "submodels": [
            {
                "modelType": "Submodel",
                "id": "urn:sm/lang",
                "idShort": "LangSm",
                "submodelElements": [
                    {
                        "modelType": "MultiLanguageProperty",
                        "idShort": "Name",
                        "value": [{"language": "en_US", "text": "Hello"}],
                    }
                ],
            }
        ],
        "conceptDescriptions": [],
    }
    aas_client.upload_json(env)

    rows = aas_client.execute_clause(
        # apoc.convert.toList: a single-entry flattened list is stored as a scalar
        # (compact_single_entry_lists), so normalise before reading it as a list.
        "MATCH (n:MultiLanguageProperty) RETURN apoc.convert.toList(n.value_language) AS langs"
    )
    langs = [l for r in rows for l in (r["langs"] or [])]
    assert "en-US" in langs
    assert "en_US" not in langs


@pytest.mark.integration
def test_fixers_off_by_default(aas_client):
    assert aas_client.fix_on_import is False  # opt-in: off unless requested
    env = {
        "assetAdministrationShells": [],
        "submodels": [
            {
                "modelType": "Submodel",
                "id": "urn:sm/nofix",
                "idShort": "NoFix",
                "submodelElements": [
                    {
                        "modelType": "MultiLanguageProperty",
                        "idShort": "Name",
                        "value": [{"language": "en_US", "text": "Hello"}],
                    }
                ],
            }
        ],
        "conceptDescriptions": [],
    }
    aas_client.upload_json(env)

    rows = aas_client.execute_clause(
        "MATCH (n:MultiLanguageProperty {idShort:'Name'}) "
        "RETURN apoc.convert.toList(n.value_language) AS langs"
    )
    langs = [l for r in rows for l in (r["langs"] or [])]
    assert "en_US" in langs  # untouched when fixing is off
