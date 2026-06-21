"""Import-time AAS fixers.

Unit tests for the fixer framework + LangStringFixer (no Neo4j), plus an
integration test that the language-tag fix is applied on the real import path.
"""

import pytest

from aas_mapping.aas_neo4j_adapter.fixers import (
    LangStringFixer,
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


# ---------------------------------------------------------------------------
# Integration test (requires live Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_language_tag_fixed_on_import(aas_client):
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
        "MATCH (n:MultiLanguageProperty) RETURN n.value_language AS langs"
    )
    langs = [l for r in rows for l in (r["langs"] or [])]
    assert "en-US" in langs
    assert "en_US" not in langs
