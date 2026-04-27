"""
Integration tests for AASQL → Cypher → Neo4j query execution.

Requires a live Neo4j instance at bolt://localhost:7687 (credentials: neo4j/12345678).
All tests are skipped automatically if Neo4j is unreachable (via the ``aas_client``
fixture in conftest.py, which also wipes the database before and after each test).

Structure
---------
TestSubmodelQueries          $sm queries against Submodel/maximal.json
TestPropertySmeQueries       $sme queries against Property/maximal.json
TestMlpQueries               #language queries against MultiLanguageProperty/maximal.json
TestNestedSmeQueries         nested path against SubmodelElementCollection/maximal.json
TestAasQueries               $aas queries against AssetAdministrationShell/maximal.json
TestCdQueries                $cd queries against ConceptDescription/maximal.json
TestMatchCorrelated          $match vs $and correlated list semantics (synthetic data)
"""

import json
import pytest
from pathlib import Path

from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import convert_aasql_to_cypher

_JSON_DIR = Path(__file__).parent / "test_data" / "Expected_JSON"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(rel_path: str) -> dict:
    with open(_JSON_DIR / rel_path, encoding="utf-8") as f:
        return json.load(f)


def _run_query(client, query: dict) -> tuple[list, str]:
    """
    Compile AASQL *query* to Cypher and execute it.

    Returns ``(records, return_var)`` where *records* is a (possibly empty)
    list of ``neo4j.Record`` objects and *return_var* is the name of the
    variable in the ``RETURN`` clause (e.g. ``"sm0"``, ``"aas"``, ``"cd"``).
    """
    cypher = convert_aasql_to_cypher(query)
    records = client.execute_clause(cypher) or []
    return_var = cypher.rsplit("RETURN ", 1)[-1].strip()
    return records, return_var


def _ids(records: list, return_var: str) -> set:
    """Return the set of ``id`` property values from the returned nodes."""
    return {
        rec[return_var]["id"]
        for rec in records
        if rec[return_var].get("id") is not None
    }


# ---------------------------------------------------------------------------
# $sm queries
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSubmodelQueries:
    """AASQL ``$sm`` queries against Submodel/maximal.json."""

    _SM_ID = "something_48c66017"
    _SM_IDSHORT = "fiZ"
    _SM_SEM_ID = "urn:an-example06:3dc4e5fa"

    def _import(self, client):
        client.upload_json(_load("Submodel/maximal.json"))

    def test_sm_id_hit(self, aas_client):
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$sm#id"}, {"$strVal": self._SM_ID}]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_sm_idshort_hit(self, aas_client):
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$sm#idShort"}, {"$strVal": self._SM_IDSHORT}]}
        })
        assert len(records) == 1
        assert records[0][var]["idShort"] == self._SM_IDSHORT

    def test_sm_semantic_id_hit(self, aas_client):
        """Semantic ID shorthand: $sm#semanticId resolves to keys_value[0]."""
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$sm#semanticId"}, {"$strVal": self._SM_SEM_ID}]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_sm_id_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$sm#id"}, {"$strVal": "urn:nonexistent"}]}
        })
        assert records == []

    def test_sm_ne_operator(self, aas_client):
        """$ne: idShort != 'wrong' — the actual node still matches."""
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$ne": [{"$field": "$sm#idShort"}, {"$strVal": "wrong"}]}
        })
        assert len(records) == 1

    def test_sm_starts_with_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$starts-with": [{"$field": "$sm#idShort"}, {"$strVal": "f"}]}
        })
        assert len(records) == 1

    def test_sm_contains_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$contains": [{"$field": "$sm#idShort"}, {"$strVal": "i"}]}
        })
        assert len(records) == 1

    def test_sm_contains_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$contains": [{"$field": "$sm#idShort"}, {"$strVal": "xyz_not_there"}]}
        })
        assert records == []


# ---------------------------------------------------------------------------
# $sme queries against a Property element
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPropertySmeQueries:
    """AASQL ``$sme`` queries against Property/maximal.json."""

    _SM_ID = "something_48c66017"
    _SME_IDSHORT = "PiXO1wyHierj"
    _SME_VALUE = "0061707"
    _SME_VALUE_TYPE = "xs:decimal"
    _SME_SEM_ID = "urn:something00:f4547d0c"

    def _import(self, client):
        client.upload_json(_load("Property/maximal.json"))

    def test_sme_value_hit(self, aas_client):
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#value"},
                {"$strVal": self._SME_VALUE},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_sme_value_type_hit(self, aas_client):
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#valueType"},
                {"$strVal": self._SME_VALUE_TYPE},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_sme_idshort_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#idShort"},
                {"$strVal": self._SME_IDSHORT},
            ]}
        })
        assert len(records) == 1

    def test_sme_semantic_id_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#semanticId"},
                {"$strVal": self._SME_SEM_ID},
            ]}
        })
        assert len(records) == 1

    def test_sme_value_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#value"},
                {"$strVal": "wrong_value"},
            ]}
        })
        assert records == []

    def test_and_sm_id_and_sme_value(self, aas_client):
        """$and combines $sm and $sme conditions sharing the same Submodel node."""
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$and": [
                {"$eq": [{"$field": "$sm#id"}, {"$strVal": self._SM_ID}]},
                {"$eq": [{"$field": f"$sme.{self._SME_IDSHORT}#value"}, {"$strVal": self._SME_VALUE}]},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_and_miss_when_one_condition_fails(self, aas_client):
        """$and returns no results if any condition fails."""
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$and": [
                {"$eq": [{"$field": "$sm#id"}, {"$strVal": self._SM_ID}]},
                {"$eq": [{"$field": f"$sme.{self._SME_IDSHORT}#value"}, {"$strVal": "bad_value"}]},
            ]}
        })
        assert records == []

    def test_or_one_hit_one_miss(self, aas_client):
        """$or: one condition matches, one doesn't — result has 1 hit."""
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$or": [
                {"$eq": [{"$field": f"$sme.{self._SME_IDSHORT}#value"}, {"$strVal": self._SME_VALUE}]},
                {"$eq": [{"$field": f"$sme.{self._SME_IDSHORT}#value"}, {"$strVal": "does_not_exist"}]},
            ]}
        })
        assert len(records) == 1

    def test_not_wrong_value_returns_hit(self, aas_client):
        """$not of a non-matching value — the actual node is returned."""
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$not": {
                "$eq": [{"$field": f"$sme.{self._SME_IDSHORT}#value"}, {"$strVal": "wrong"}]
            }}
        })
        assert len(records) == 1

    def test_sme_value_starts_with(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$starts-with": [
                {"$field": f"$sme.{self._SME_IDSHORT}#value"},
                {"$strVal": "006"},
            ]}
        })
        assert len(records) == 1

    def test_sme_value_ends_with(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$ends-with": [
                {"$field": f"$sme.{self._SME_IDSHORT}#value"},
                {"$strVal": "707"},
            ]}
        })
        assert len(records) == 1

    def test_sme_value_regex_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$regex": [
                {"$field": f"$sme.{self._SME_IDSHORT}#value"},
                {"$strVal": "^00[0-9]+$"},
            ]}
        })
        assert len(records) == 1

    def test_sme_value_regex_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$regex": [
                {"$field": f"$sme.{self._SME_IDSHORT}#value"},
                {"$strVal": "^ZZ.*"},
            ]}
        })
        assert records == []


# ---------------------------------------------------------------------------
# #language queries (MultiLanguageProperty)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMlpQueries:
    """AASQL ``$sme … #language`` queries against MultiLanguageProperty/maximal.json."""

    _SME_IDSHORT = "PiXO1wyHierj"
    _LANGUAGE = "sr-Latn-QM"

    def _import(self, client):
        client.upload_json(_load("MultiLanguageProperty/maximal.json"))

    def test_mlp_language_hit(self, aas_client):
        """#language uses Cypher IN on the value_language list."""
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#language"},
                {"$strVal": self._LANGUAGE},
            ]}
        })
        assert len(records) == 1

    def test_mlp_language_miss(self, aas_client):
        """Language not present in the MLP value list → no results."""
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SME_IDSHORT}#language"},
                {"$strVal": "en"},
            ]}
        })
        assert records == []


# ---------------------------------------------------------------------------
# Nested SME path
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedSmeQueries:
    """Queries that traverse more than one SME hop (SubmodelElementCollection/maximal.json)."""

    _SM_ID = "something_48c66017"
    _SMC_IDSHORT = "PiXO1wyHierj"
    _CHILD_IDSHORT = "somethingd2cab823"

    def _import(self, client):
        client.upload_json(_load("SubmodelElementCollection/maximal.json"))

    def test_nested_child_idshort_hit(self, aas_client):
        """Two-hop path $sme.Collection.Child#idShort resolves via [:value] traversal."""
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SMC_IDSHORT}.{self._CHILD_IDSHORT}#idShort"},
                {"$strVal": self._CHILD_IDSHORT},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_nested_child_wrong_idshort_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": f"$sme.{self._SMC_IDSHORT}.{self._CHILD_IDSHORT}#idShort"},
                {"$strVal": "wrong_child"},
            ]}
        })
        assert records == []


# ---------------------------------------------------------------------------
# $aas queries
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAasQueries:
    """AASQL ``$aas`` queries against AssetAdministrationShell/maximal.json."""

    _AAS_ID = "something_142922d6"
    _AAS_IDSHORT = "PiXO1wyHierj"
    _ASSET_KIND = "NotApplicable"
    _GLOBAL_ASSET_ID = "something_eea66fa1"

    def _import(self, client):
        client.upload_json(_load("AssetAdministrationShell/maximal.json"))

    def test_aas_idshort_hit(self, aas_client):
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$aas#idShort"}, {"$strVal": self._AAS_IDSHORT}]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._AAS_ID

    def test_aas_asset_kind_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": "$aas#assetInformation.assetKind"},
                {"$strVal": self._ASSET_KIND},
            ]}
        })
        assert len(records) == 1

    def test_aas_global_asset_id_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [
                {"$field": "$aas#assetInformation.globalAssetId"},
                {"$strVal": self._GLOBAL_ASSET_ID},
            ]}
        })
        assert len(records) == 1

    def test_aas_idshort_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$aas#idShort"}, {"$strVal": "NoSuchAAS"}]}
        })
        assert records == []


# ---------------------------------------------------------------------------
# $cd queries
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCdQueries:
    """AASQL ``$cd`` queries against ConceptDescription/maximal.json."""

    _CD_ID = "something_8ccad77f"
    _CD_IDSHORT = "fiZ"

    def _import(self, client):
        client.upload_json(_load("ConceptDescription/maximal.json"))

    def test_cd_id_hit(self, aas_client):
        self._import(aas_client)
        records, var = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$cd#id"}, {"$strVal": self._CD_ID}]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._CD_ID

    def test_cd_idshort_hit(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$cd#idShort"}, {"$strVal": self._CD_IDSHORT}]}
        })
        assert len(records) == 1

    def test_cd_id_miss(self, aas_client):
        self._import(aas_client)
        records, _ = _run_query(aas_client, {
            "$condition": {"$eq": [{"$field": "$cd#id"}, {"$strVal": "urn:does-not-exist"}]}
        })
        assert records == []


# ---------------------------------------------------------------------------
# $match correlated list semantics
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMatchCorrelated:
    """
    Demonstrates the semantic difference between ``$match`` and ``$and`` for
    list traversal.

    Test data: a Submodel with a SubmodelElementCollection ("Collection") that
    has two children:
      * ChildA — value "alpha", idShort "ChildA"
      * ChildB — value "beta",  idShort "ChildB"

    ``$match([cond_A, cond_B])`` requires BOTH conditions to be satisfied by
    THE SAME child element.

    ``$and([cond_A, cond_B])`` only requires each condition to be satisfied by
    SOME (possibly different) element.
    """

    _SM_ID = "urn:test:match_correlated"

    def _env(self) -> dict:
        return {
            "submodels": [{
                "id": self._SM_ID,
                "modelType": "Submodel",
                "submodelElements": [{
                    "idShort": "Collection",
                    "modelType": "SubmodelElementCollection",
                    "value": [
                        {
                            "idShort": "ChildA",
                            "modelType": "Property",
                            "value": "alpha",
                            "valueType": "xs:string",
                        },
                        {
                            "idShort": "ChildB",
                            "modelType": "Property",
                            "value": "beta",
                            "valueType": "xs:string",
                        },
                    ],
                }],
            }]
        }

    # --- $and: uncorrelated, finds the Submodel ---

    def test_and_different_elements_hit(self, aas_client):
        """
        ``$and`` with two wildcard paths → each wildcard gets a fresh variable.
        ChildA satisfies value='alpha', ChildB satisfies value='beta' →
        the Submodel matches because both conditions are satisfied by SOME child.
        """
        aas_client.upload_json(self._env())
        records, var = _run_query(aas_client, {
            "$condition": {"$and": [
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "alpha"}]},
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "beta"}]},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_and_both_values_missing_miss(self, aas_client):
        """``$and`` with two values that don't exist → no results."""
        aas_client.upload_json(self._env())
        records, _ = _run_query(aas_client, {
            "$condition": {"$and": [
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "gamma"}]},
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "delta"}]},
            ]}
        })
        assert records == []

    # --- $match: correlated, same element must satisfy all conditions ---

    def test_match_impossible_on_single_element_miss(self, aas_client):
        """
        ``$match`` WHERE: sme.value = 'alpha' AND sme.value = 'beta'.
        No single element can have two different values → no results.
        """
        aas_client.upload_json(self._env())
        records, _ = _run_query(aas_client, {
            "$condition": {"$match": [
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "alpha"}]},
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "beta"}]},
            ]}
        })
        assert records == []

    def test_match_same_element_both_conditions_hit(self, aas_client):
        """
        ``$match`` WHERE: sme.value = 'alpha' AND sme.idShort = 'ChildA'.
        ChildA satisfies BOTH conditions → 1 result.
        """
        aas_client.upload_json(self._env())
        records, var = _run_query(aas_client, {
            "$condition": {"$match": [
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "alpha"}]},
                {"$eq": [{"$field": "$sme.Collection[]#idShort"}, {"$strVal": "ChildA"}]},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID

    def test_match_cross_element_miss(self, aas_client):
        """
        ``$match`` WHERE: sme.value = 'alpha' AND sme.idShort = 'ChildB'.
        ChildA has value='alpha' (idShort='ChildA'); ChildB has idShort='ChildB' (value='beta').
        No single element satisfies both → no results.
        This is the key semantic difference from ``$and``.
        """
        aas_client.upload_json(self._env())
        records, _ = _run_query(aas_client, {
            "$condition": {"$match": [
                {"$eq": [{"$field": "$sme.Collection[]#value"}, {"$strVal": "alpha"}]},
                {"$eq": [{"$field": "$sme.Collection[]#idShort"}, {"$strVal": "ChildB"}]},
            ]}
        })
        assert records == []

    def test_match_degenerate_no_common_list_prefix(self, aas_client):
        """
        ``$match`` without a shared list wildcard degenerates to ``$and``.
        Uses $sm#id (no list traversal) + direct $sme value.
        """
        aas_client.upload_json(self._env())
        records, var = _run_query(aas_client, {
            "$condition": {"$match": [
                {"$eq": [{"$field": "$sm#id"}, {"$strVal": self._SM_ID}]},
                {"$eq": [{"$field": "$sme.Collection.ChildA#value"}, {"$strVal": "alpha"}]},
            ]}
        })
        assert len(records) == 1
        assert records[0][var]["id"] == self._SM_ID
