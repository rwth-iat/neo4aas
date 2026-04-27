"""
Tests for the AASQL → Cypher compiler (ast_to_cypher.py).

Test structure:
  TestParseField         — unit tests for the field path parser
  TestSimpleComparisons  — single-condition queries for every operator and root type
  TestLogicalOperators   — $and, $or, $not, nested combinations
  TestMatchOperator      — $match with correlated list traversal
  TestAttributeEdgeCases — semanticId, language/MLP, assetInformation, cast operators
  TestExamplesEndToEnd   — parametrised over all 17 example JSON → .cypher pairs
"""

import json
import os
import textwrap
import unittest
from pathlib import Path

from aas_mapping.aas_neo4j_adapter.querification.ast_nodes import (
    And, BooleanValue, BoolCast, Condition, Contains, DateTimeCast, Eq, Field,
    Ge, Gt, Le, Lt, Match, Ne, Not, NumCast, NumberValue, Or, Regex,
    StartsWith, EndsWith, StrCast, StringValue,
)
from aas_mapping.aas_neo4j_adapter.querification.ast_to_cypher import (
    CypherQueryBuilder, FieldPath, SmeIndex, SmeStep, SmeWildcard,
    _longest_common_list_prefix, _normalize_mlp_segments, converter,
    parse_field,
)
from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import (
    convert_aasql_to_cypher,
)


def _cypher(query_dict: dict) -> str:
    return convert_aasql_to_cypher(query_dict)


def _cond(expr) -> str:
    return converter(Condition(expr))


# ---------------------------------------------------------------------------
# parse_field
# ---------------------------------------------------------------------------

class TestParseField(unittest.TestCase):

    def test_aas_simple_attribute(self):
        fp = parse_field("$aas#idShort")
        self.assertEqual(fp, FieldPath("$aas", [], "idShort"))

    def test_aas_id(self):
        fp = parse_field("$aas#id")
        self.assertEqual(fp, FieldPath("$aas", [], "id"))

    def test_sm_simple(self):
        fp = parse_field("$sm#idShort")
        self.assertEqual(fp, FieldPath("$sm", [], "idShort"))

    def test_sm_semantic_id(self):
        fp = parse_field("$sm#semanticId")
        self.assertEqual(fp, FieldPath("$sm", [], "semanticId"))

    def test_cd_simple(self):
        fp = parse_field("$cd#id")
        self.assertEqual(fp, FieldPath("$cd", [], "id"))

    def test_sme_bare(self):
        fp = parse_field("$sme#value")
        self.assertEqual(fp, FieldPath("$sme", [], "value"))

    def test_sme_named_step(self):
        fp = parse_field("$sme.Weight#value")
        self.assertEqual(fp, FieldPath("$sme", [SmeStep("Weight")], "value"))

    def test_sme_wildcard(self):
        fp = parse_field("$sme.Items[]#value")
        self.assertEqual(fp, FieldPath("$sme", [SmeStep("Items"), SmeWildcard()], "value"))

    def test_sme_indexed(self):
        fp = parse_field("$sme.Items[0]#value")
        self.assertEqual(fp, FieldPath("$sme", [SmeStep("Items"), SmeIndex(0)], "value"))

    def test_sme_deep_wildcard(self):
        fp = parse_field("$sme.A[].B.C#idShort")
        self.assertEqual(
            fp,
            FieldPath("$sme", [SmeStep("A"), SmeWildcard(), SmeStep("B"), SmeStep("C")], "idShort"),
        )

    def test_sme_multi_level(self):
        fp = parse_field("$sme.X.Y.Z#value")
        self.assertEqual(
            fp,
            FieldPath("$sme", [SmeStep("X"), SmeStep("Y"), SmeStep("Z")], "value"),
        )

    def test_sme_language_with_wildcard(self):
        fp = parse_field("$sme.Names[]#language")
        self.assertEqual(
            fp,
            FieldPath("$sme", [SmeStep("Names"), SmeWildcard()], "language"),
        )

    def test_aas_asset_info(self):
        fp = parse_field("$aas#assetInformation.assetKind")
        self.assertEqual(fp, FieldPath("$aas", [], "assetInformation.assetKind"))


# ---------------------------------------------------------------------------
# _longest_common_list_prefix helper
# ---------------------------------------------------------------------------

class TestCommonPrefix(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(_longest_common_list_prefix([]), [])

    def test_no_wildcards_returns_empty(self):
        fps = [
            FieldPath("$sme", [SmeStep("A"), SmeStep("B")], "value"),
            FieldPath("$sme", [SmeStep("A"), SmeStep("C")], "value"),
        ]
        self.assertEqual(_longest_common_list_prefix(fps), [])

    def test_wildcard_prefix(self):
        fps = [
            FieldPath("$sme", [SmeStep("Items"), SmeWildcard(), SmeStep("Name")], "value"),
            FieldPath("$sme", [SmeStep("Items"), SmeWildcard(), SmeStep("Status")], "value"),
        ]
        result = _longest_common_list_prefix(fps)
        self.assertEqual(result, [SmeStep("Items"), SmeWildcard()])

    def test_index_prefix(self):
        fps = [
            FieldPath("$sme", [SmeStep("X"), SmeIndex(0), SmeStep("A")], "value"),
            FieldPath("$sme", [SmeStep("X"), SmeIndex(0), SmeStep("B")], "value"),
        ]
        result = _longest_common_list_prefix(fps)
        self.assertEqual(result, [SmeStep("X"), SmeIndex(0)])

    def test_diverges_before_wildcard(self):
        fps = [
            FieldPath("$sme", [SmeStep("A"), SmeWildcard(), SmeStep("X")], "value"),
            FieldPath("$sme", [SmeStep("B"), SmeWildcard(), SmeStep("Y")], "value"),
        ]
        # First segments differ → no common prefix
        self.assertEqual(_longest_common_list_prefix(fps), [])

    def test_stops_at_first_wildcard(self):
        fps = [
            FieldPath("$sme", [SmeStep("A"), SmeWildcard(), SmeStep("B"), SmeWildcard()], "value"),
            FieldPath("$sme", [SmeStep("A"), SmeWildcard(), SmeStep("C"), SmeWildcard()], "value"),
        ]
        # Should stop at the first Wildcard
        result = _longest_common_list_prefix(fps)
        self.assertEqual(result, [SmeStep("A"), SmeWildcard()])


# ---------------------------------------------------------------------------
# _normalize_mlp_segments helper
# ---------------------------------------------------------------------------

class TestNormalizeMlpSegments(unittest.TestCase):

    def test_drops_trailing_wildcard_for_language(self):
        segs = [SmeStep("Names"), SmeWildcard()]
        result = _normalize_mlp_segments(segs, "language")
        self.assertEqual(result, [SmeStep("Names")])

    def test_keeps_wildcard_for_other_attr(self):
        segs = [SmeStep("Items"), SmeWildcard()]
        result = _normalize_mlp_segments(segs, "value")
        self.assertEqual(result, segs)

    def test_no_wildcard_unchanged(self):
        segs = [SmeStep("A"), SmeStep("B")]
        result = _normalize_mlp_segments(segs, "language")
        self.assertEqual(result, segs)


# ---------------------------------------------------------------------------
# Simple comparisons — all operators and root types
# ---------------------------------------------------------------------------

class TestSimpleComparisons(unittest.TestCase):

    # --- $aas root ---

    def test_aas_idshort_eq(self):
        result = _cond(Eq(Field("$aas#idShort"), StringValue("MyAAS")))
        self.assertIn("(aas:AssetAdministrationShell)", result)
        self.assertIn("aas.idShort = 'MyAAS'", result)
        self.assertIn("RETURN aas", result)

    def test_aas_id_ne(self):
        result = _cond(Ne(Field("$aas#id"), StringValue("urn:x")))
        self.assertIn("aas.id <> 'urn:x'", result)

    def test_aas_asset_info_asset_kind(self):
        result = _cond(Eq(Field("$aas#assetInformation.assetKind"), StringValue("Instance")))
        self.assertIn("-[:assetInformation]->", result)
        self.assertIn(".assetKind = 'Instance'", result)

    def test_aas_asset_info_global_asset_id(self):
        result = _cond(Eq(Field("$aas#assetInformation.globalAssetId"), StringValue("urn:g")))
        self.assertIn(".globalAssetId = 'urn:g'", result)

    # --- $sm root ---

    def test_sm_idshort_eq(self):
        result = _cond(Eq(Field("$sm#idShort"), StringValue("TechnicalData")))
        self.assertIn("(sm0:Submodel)", result)
        self.assertIn("sm0.idShort = 'TechnicalData'", result)
        self.assertIn("RETURN sm0", result)

    def test_sm_id(self):
        result = _cond(Eq(Field("$sm#id"), StringValue("urn:sm")))
        self.assertIn("sm0.id = 'urn:sm'", result)

    def test_sm_semantic_id(self):
        result = _cond(Eq(Field("$sm#semanticId"), StringValue("urn:sem")))
        self.assertIn("-[:semanticId]->", result)
        self.assertIn(".keys_value[0] = 'urn:sem'", result)

    # --- $sme root ---

    def test_sme_value_eq(self):
        result = _cond(Eq(Field("$sme.Weight#value"), NumberValue(100)))
        self.assertIn("(sm0:Submodel)", result)
        self.assertIn("(sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Weight'})", result)
        self.assertIn("sme0.value = 100", result)
        self.assertIn("RETURN sm0", result)

    def test_sme_ne(self):
        result = _cond(Ne(Field("$sme.Material#value"), StringValue("Plastic")))
        self.assertIn("sme0.value <> 'Plastic'", result)

    def test_sme_gt(self):
        result = _cond(Gt(Field("$sme.Temperature#value"), NumberValue(50)))
        self.assertIn("sme0.value > 50", result)

    def test_sme_ge(self):
        result = _cond(Ge(Field("$sme.Weight#value"), NumberValue(100)))
        self.assertIn("sme0.value >= 100", result)

    def test_sme_lt(self):
        result = _cond(Lt(Field("$sme.Pressure#value"), NumberValue(200)))
        self.assertIn("sme0.value < 200", result)

    def test_sme_le(self):
        result = _cond(Le(Field("$sme.Weight#value"), NumberValue(50)))
        self.assertIn("sme0.value <= 50", result)

    def test_sme_contains(self):
        result = _cond(Contains(Field("$sme.Description#value"), StringValue("high-quality")))
        self.assertIn("CONTAINS", result)
        self.assertIn("'high-quality'", result)

    def test_sme_starts_with(self):
        result = _cond(StartsWith(Field("$sme.Code#value"), StringValue("ABC-")))
        self.assertIn("STARTS WITH 'ABC-'", result)

    def test_sme_ends_with(self):
        result = _cond(EndsWith(Field("$sme.Code#value"), StringValue("-XYZ")))
        self.assertIn("ENDS WITH '-XYZ'", result)

    def test_sme_regex(self):
        result = _cond(Regex(Field("$sme.Serial#value"), StringValue("SN[0-9]+")))
        self.assertIn("=~ 'SN[0-9]+'", result)

    def test_sme_idshort(self):
        result = _cond(Eq(Field("$sme.X#idShort"), StringValue("Target")))
        self.assertIn("sme0.idShort = 'Target'", result)

    def test_sme_value_type(self):
        result = _cond(Eq(Field("$sme.X#valueType"), StringValue("xs:double")))
        self.assertIn("sme0.valueType = 'xs:double'", result)

    def test_sme_bare_value(self):
        result = _cond(Lt(Field("$sme#value"), NumberValue(100)))
        self.assertIn("-[:submodelElements]->(sme0:SubmodelElement)", result)
        self.assertIn("sme0.value < 100", result)

    def test_sme_semantic_id(self):
        result = _cond(Eq(Field("$sme#semanticId"), StringValue("urn:sem")))
        self.assertIn("-[:submodelElements]->(sme0:SubmodelElement)", result)
        self.assertIn("-[:semanticId]->(sid0)", result)
        self.assertIn("sid0.keys_value[0] = 'urn:sem'", result)

    # --- $cd root ---

    def test_cd_idshort(self):
        result = _cond(Eq(Field("$cd#idShort"), StringValue("MyCD")))
        self.assertIn("(cd:ConceptDescription)", result)
        self.assertIn("cd.idShort = 'MyCD'", result)
        self.assertIn("RETURN cd", result)

    def test_cd_id(self):
        result = _cond(Eq(Field("$cd#id"), StringValue("urn:cd")))
        self.assertIn("cd.id = 'urn:cd'", result)


# ---------------------------------------------------------------------------
# Logical operators
# ---------------------------------------------------------------------------

class TestLogicalOperators(unittest.TestCase):

    def test_and_two_sme_conditions(self):
        result = _cond(And([
            Eq(Field("$sme.Color#value"), StringValue("Blue")),
            Gt(Field("$sme.Size#value"), NumberValue(50)),
        ]))
        # Both share sm0
        self.assertIn("(sm0:Submodel)", result)
        self.assertIn("(sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Color'})", result)
        self.assertIn("(sm0)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Size'})", result)
        self.assertIn("sme0.value = 'Blue' AND sme1.value > 50", result)

    def test_and_sm_and_sme_share_sm_var(self):
        result = _cond(And([
            Eq(Field("$sm#idShort"), StringValue("TechnicalData")),
            Lt(Field("$sme.Pressure#value"), NumberValue(200)),
        ]))
        # $sm and $sme share sm0
        self.assertIn("(sm0:Submodel)", result)
        self.assertIn("sm0.idShort = 'TechnicalData'", result)
        self.assertIn("sme0.value < 200", result)
        # Only one Submodel pattern
        self.assertEqual(result.count("sm0:Submodel"), 1)

    def test_or_two_sme_conditions(self):
        result = _cond(Or([
            Eq(Field("$sme.Material#value"), StringValue("Metal")),
            Le(Field("$sme.Weight#value"), NumberValue(50)),
        ]))
        self.assertIn(" OR ", result)
        self.assertIn("sme0.value = 'Metal'", result)
        self.assertIn("sme1.value <= 50", result)

    def test_or_sme_and_sm(self):
        result = _cond(Or([
            Contains(Field("$sme.Description#value"), StringValue("urgent")),
            Eq(Field("$sm#idShort"), StringValue("MaintenanceLog")),
        ]))
        # Both reference sm0
        self.assertEqual(result.count("sm0:Submodel"), 1)
        self.assertIn("sme0.value CONTAINS 'urgent' OR sm0.idShort = 'MaintenanceLog'", result)

    def test_not(self):
        result = _cond(Not(Eq(Field("$sme.Status#value"), StringValue("Inactive"))))
        self.assertIn("NOT (sme0.value = 'Inactive')", result)

    def test_nested_and_or(self):
        result = _cond(And([
            Or([
                Eq(Field("$sme.Material#value"), StringValue("Steel")),
                Eq(Field("$sme.Material#value"), StringValue("Aluminum")),
            ]),
            Lt(Field("$sme.Weight#value"), NumberValue(200)),
        ]))
        # Path cache: both Material references share sme0; Weight gets sme1
        self.assertIn("(sme0.value = 'Steel' OR sme0.value = 'Aluminum')", result)
        self.assertIn("sme1.value < 200", result)


# ---------------------------------------------------------------------------
# $match operator
# ---------------------------------------------------------------------------

class TestMatchOperator(unittest.TestCase):

    def test_match_simple_shared_wildcard(self):
        """$match with two paths sharing a named[] prefix → shared sme1 node."""
        result = _cond(Match([
            Eq(Field("$sme.Items[].Name#value"), StringValue("Motor")),
            Eq(Field("$sme.Items[].Status#value"), StringValue("Active")),
        ]))
        # Shared prefix emitted as two separate per-hop MATCH elements
        self.assertIn("(sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Items'})", result)
        self.assertIn("(sme0)-[:value]->(sme1:SubmodelElement)", result)
        # Branches from sme1
        self.assertIn("(sme1)-[:value]->(sme2:SubmodelElement {idShort: 'Name'})", result)
        self.assertIn("(sme1)-[:value]->(sme3:SubmodelElement {idShort: 'Status'})", result)
        self.assertIn("sme2.value = 'Motor' AND sme3.value = 'Active'", result)
        # sme0 / Items only appears once
        self.assertEqual(result.count("idShort: 'Items'"), 1)

    def test_match_no_common_prefix_degenerates_to_and(self):
        """$match without shared wildcard → treated as $and."""
        result = _cond(Match([
            Eq(Field("$sm#idShort"), StringValue("TechnicalData")),
            Eq(Field("$sme.X#value"), StringValue("foo")),
        ]))
        self.assertIn("sm0.idShort = 'TechnicalData'", result)
        self.assertIn("sme0.value = 'foo'", result)
        # All conditions joined with AND
        self.assertIn(" AND ", result)

    def test_match_mlp_language_in_branch(self):
        """$match with a branch ending in []#language uses IN operator on value_language."""
        result = _cond(Match([
            Eq(Field("$sme.Docs[].Class#value"), StringValue("03-01")),
            Eq(Field("$sme.Docs[].Names[]#language"), StringValue("nl")),
        ]))
        # Shared prefix: Docs[]
        self.assertIn("idShort: 'Docs'", result)
        # Class branch
        self.assertIn("idShort: 'Class'", result)
        # Names branch: MultiLanguageProperty node, no extra wildcard node
        self.assertIn(":MultiLanguageProperty {idShort: 'Names'}", result)
        # IN operator for language list
        self.assertIn("'nl' IN", result)
        self.assertIn(".value_language", result)

    def test_match_three_conditions(self):
        """$match with three conditions sharing a wildcard prefix."""
        result = _cond(Match([
            Eq(Field("$sme.Items[].A#value"), StringValue("x")),
            Eq(Field("$sme.Items[].B#value"), StringValue("y")),
            Eq(Field("$sme.Items[].C#value"), StringValue("z")),
        ]))
        # The shared node (sme1 after Items[]) should appear as a branch start
        self.assertIn("sme1", result)
        self.assertIn("AND", result)
        # Items path emitted once
        self.assertEqual(result.count("idShort: 'Items'"), 1)

    def test_and_with_two_matches(self):
        """$and wrapping two $match operators."""
        result = _cond(And([
            Match([
                Eq(Field("$sm#idShort"), StringValue("TD")),
                Eq(Field("$sme.PCI.Item.ClassId#value"), StringValue("27-37")),
            ]),
            Match([
                Eq(Field("$sm#idShort"), StringValue("TD")),
                Lt(Field("$sme#value"), NumberValue(100)),
            ]),
        ]))
        # Single shared Submodel
        self.assertEqual(result.count("sm0:Submodel"), 1)
        self.assertIn("sm0.idShort = 'TD'", result)
        # The bare $sme#value condition (from second $match) maps to some smeN.value < 100
        self.assertIn(".value < 100", result)


# ---------------------------------------------------------------------------
# Attribute edge cases
# ---------------------------------------------------------------------------

class TestAttributeEdgeCases(unittest.TestCase):

    def test_semantic_id_shorthand(self):
        """$sm#semanticId → .keys_value[0]."""
        result = _cond(Eq(Field("$sm#semanticId"), StringValue("urn:sem")))
        self.assertIn("-[:semanticId]->(sid0)", result)
        self.assertIn("sid0.keys_value[0] = 'urn:sem'", result)

    def test_semantic_id_sme(self):
        result = _cond(Eq(Field("$sme#semanticId"), StringValue("urn:sem")))
        self.assertIn("-[:semanticId]->(sid0)", result)
        self.assertIn("sid0.keys_value[0] = 'urn:sem'", result)

    def test_language_attribute_uses_in(self):
        result = _cond(Eq(Field("$sme.Names#language"), StringValue("en")))
        self.assertIn(".value_language", result)
        self.assertIn("'en' IN", result)

    def test_mlp_terminal_label(self):
        result = _cond(Eq(Field("$sme.Names#language"), StringValue("en")))
        self.assertIn(":MultiLanguageProperty", result)

    def test_language_wildcard_drops_extra_node(self):
        """$sme.MLP[]#language should not create an extra unnamed node."""
        result = _cond(Eq(Field("$sme.MLP[]#language"), StringValue("de")))
        self.assertIn(":MultiLanguageProperty {idShort: 'MLP'}", result)
        # No extra anonymous node after MLP
        mlp_count = result.count("MultiLanguageProperty")
        self.assertEqual(mlp_count, 1)

    def test_asset_info_asset_kind(self):
        result = _cond(Eq(
            Field("$aas#assetInformation.assetKind"), StringValue("Instance")
        ))
        self.assertIn("-[:assetInformation]->(ai0:AssetInformation)", result)
        self.assertIn("ai0.assetKind = 'Instance'", result)

    def test_asset_info_asset_type(self):
        result = _cond(Eq(
            Field("$aas#assetInformation.assetType"), StringValue("robot")
        ))
        self.assertIn("ai0.assetType = 'robot'", result)

    def test_asset_info_global_asset_id(self):
        result = _cond(Eq(
            Field("$aas#assetInformation.globalAssetId"), StringValue("urn:g")
        ))
        self.assertIn("ai0.globalAssetId = 'urn:g'", result)

    def test_str_cast(self):
        result = _cond(Eq(
            StrCast(Field("$sme.X#value")), StringValue("42")
        ))
        self.assertIn("toString(sme0.value) = '42'", result)

    def test_num_cast(self):
        result = _cond(Eq(
            NumCast(Field("$sme.X#value")), NumberValue(42)
        ))
        self.assertIn("toInteger(sme0.value) = 42", result)

    def test_bool_cast(self):
        result = _cond(Eq(
            BoolCast(Field("$sme.X#value")), StringValue("true")
        ))
        self.assertIn("toBoolean(sme0.value)", result)

    def test_datetime_cast(self):
        result = _cond(Eq(
            DateTimeCast(Field("$sme.X#value")), StringValue("2024-01-01T00:00:00Z")
        ))
        self.assertIn("date(sme0.value)", result)

    def test_hex_cast_raises(self):
        from aas_mapping.aas_neo4j_adapter.querification.ast_nodes import HexCast
        with self.assertRaises(NotImplementedError):
            _cond(Eq(HexCast(Field("$sme.X#value")), StringValue("FF")))

    def test_time_cast_raises(self):
        from aas_mapping.aas_neo4j_adapter.querification.ast_nodes import TimeCast
        with self.assertRaises(NotImplementedError):
            _cond(Eq(TimeCast(Field("$sme.X#value")), StringValue("12:00:00")))

    def test_boolean_value_false(self):
        result = _cond(Eq(Field("$sme.Active#value"), BooleanValue(False)))
        self.assertIn("false", result)

    def test_float_number_value(self):
        result = _cond(Gt(Field("$sme.X#value"), NumberValue(3.14)))
        self.assertIn("3.14", result)

    def test_integer_number_value(self):
        result = _cond(Gt(Field("$sme.X#value"), NumberValue(5.0)))
        self.assertIn("5", result)
        self.assertNotIn("5.0", result)


# ---------------------------------------------------------------------------
# MATCH clause structure tests
# ---------------------------------------------------------------------------

class TestMatchClauses(unittest.TestCase):

    def test_shared_submodel_not_duplicated(self):
        """Multiple $sme conditions produce exactly one (smN:Submodel) clause."""
        result = _cond(And([
            Eq(Field("$sme.A#value"), StringValue("a")),
            Eq(Field("$sme.B#value"), StringValue("b")),
            Eq(Field("$sme.C#value"), StringValue("c")),
        ]))
        self.assertEqual(result.count("sm0:Submodel"), 1)

    def test_return_variable_is_sm_for_sme_query(self):
        result = _cond(Eq(Field("$sme.X#value"), StringValue("v")))
        self.assertTrue(result.strip().endswith("RETURN sm0"))

    def test_return_variable_is_aas(self):
        result = _cond(Eq(Field("$aas#idShort"), StringValue("x")))
        self.assertTrue(result.strip().endswith("RETURN aas"))

    def test_return_variable_is_cd(self):
        result = _cond(Eq(Field("$cd#id"), StringValue("urn:x")))
        self.assertTrue(result.strip().endswith("RETURN cd"))

    def test_sme_path_uses_submodelelements_first_then_value(self):
        result = _cond(Eq(Field("$sme.A.B#value"), StringValue("x")))
        self.assertIn("submodelElements", result)
        self.assertIn("-[:value]->", result)
        # First hop must be submodelElements
        first_match = result.split("\n")[1]  # second line after "MATCH"
        self.assertIn("submodelElements", first_match)


# ---------------------------------------------------------------------------
# End-to-end: all 17 example JSON → expected .cypher files
# ---------------------------------------------------------------------------

class TestExamplesEndToEnd(unittest.TestCase):

    QUERY_DIR = Path("aas_mapping/examples/queries")

    def _pairs(self):
        for fname in sorted(os.listdir(self.QUERY_DIR)):
            if fname.endswith(".json"):
                cypher_fname = fname.replace(".json", ".cypher")
                json_path = self.QUERY_DIR / fname
                cypher_path = self.QUERY_DIR / cypher_fname
                if cypher_path.is_file():
                    yield fname, json_path, cypher_path

    def test_all_examples(self):
        found = 0
        for fname, json_path, cypher_path in self._pairs():
            with self.subTest(query=fname):
                with open(json_path) as f:
                    query = json.load(f)
                with open(cypher_path) as f:
                    expected = f.read()
                result = convert_aasql_to_cypher(query)
                self.assertEqual(
                    result, expected,
                    f"\n\n{fname}:\n\nExpected:\n{expected}\n\nGot:\n{result}",
                )
            found += 1
        self.assertGreater(found, 0, "No example pairs found")


# ---------------------------------------------------------------------------
# Config-driven property name resolution
# ---------------------------------------------------------------------------

class TestConfigDrivenProps(unittest.TestCase):
    """Tests for Neo4jModelConfig-aware property name resolution."""

    def _make_config(self, list_of_dicts=None):
        from aas_mapping.aas_neo4j_adapter.base import Neo4jModelConfig
        return Neo4jModelConfig(
            default_optimization_clauses=[],
            deduplicated_object_types=[],
            list_of_dicts_prop_as_multiple_list_props=list_of_dicts or {},
            dict_prop_as_multiple_props={},
            virtual_relationships=[],
            keys_to_ignore=[],
            all_list_item_relationships_have_index=False,
            list_item_relationships_with_index={},
        )

    def _build(self, config=None):
        return CypherQueryBuilder(config=config)

    # --- No config: convention-based, no validation ---

    def test_no_config_semantic_id_works(self):
        """Without config, semanticId access uses keys_value by convention."""
        result = _cond(Eq(Field("$sm#semanticId"), StringValue("urn:sem")))
        self.assertIn("keys_value[0]", result)

    def test_no_config_language_works(self):
        """Without config, #language uses value_language by convention."""
        result = _cond(Eq(Field("$sme.Names#language"), StringValue("en")))
        self.assertIn("value_language", result)

    # --- Config present and correct: no error ---

    def test_config_with_reference_keys_passes(self):
        """Config that includes Reference.keys → semanticId compiles without error."""
        config = self._make_config({"Reference": ["keys"]})
        builder = self._build(config)
        result = builder.compile(Condition(Eq(Field("$sm#semanticId"), StringValue("urn:sem"))))
        self.assertIn("keys_value[0]", result)

    def test_config_with_mlp_value_passes(self):
        """Config that includes MultiLanguageProperty.value → #language compiles without error."""
        config = self._make_config({"MultiLanguageProperty": ["value"]})
        builder = self._build(config)
        result = builder.compile(Condition(Eq(Field("$sme.Names#language"), StringValue("en"))))
        self.assertIn("value_language", result)

    def test_config_with_both_passes(self):
        """Config with Reference.keys + MultiLanguageProperty.value → both attributes compile."""
        config = self._make_config({
            "Reference": ["keys"],
            "MultiLanguageProperty": ["value"],
        })
        builder = self._build(config)
        result_sem = builder.compile(
            Condition(Eq(Field("$sm#semanticId"), StringValue("urn:sem")))
        )
        builder2 = self._build(config)
        result_lang = builder2.compile(
            Condition(Eq(Field("$sme.Names#language"), StringValue("en")))
        )
        self.assertIn("keys_value[0]", result_sem)
        self.assertIn("value_language", result_lang)

    # --- Config present but incomplete: ValueError raised ---

    def test_config_missing_reference_keys_raises_on_semantic_id(self):
        """Config without Reference.keys → accessing semanticId raises ValueError."""
        config = self._make_config({})   # Reference.keys not configured
        builder = self._build(config)
        with self.assertRaises(ValueError) as ctx:
            builder.compile(Condition(Eq(Field("$sm#semanticId"), StringValue("urn:sem"))))
        self.assertIn("Reference", str(ctx.exception))
        self.assertIn("keys", str(ctx.exception))

    def test_config_missing_mlp_value_raises_on_language(self):
        """Config without MultiLanguageProperty.value → accessing #language raises ValueError."""
        config = self._make_config({})   # MultiLanguageProperty.value not configured
        builder = self._build(config)
        with self.assertRaises(ValueError) as ctx:
            builder.compile(Condition(Eq(Field("$sme.Names#language"), StringValue("en"))))
        self.assertIn("MultiLanguageProperty", str(ctx.exception))
        self.assertIn("value", str(ctx.exception))

    def test_config_reference_other_prop_does_not_satisfy_keys(self):
        """Reference.description configured but not keys → still raises for semanticId."""
        config = self._make_config({"Reference": ["description"]})
        builder = self._build(config)
        with self.assertRaises(ValueError):
            builder.compile(Condition(Eq(Field("$sm#semanticId"), StringValue("urn:sem"))))

    # --- convert_aasql_to_cypher accepts config ---

    def test_convert_aasql_to_cypher_passes_config(self):
        """convert_aasql_to_cypher() forwards config to compiler."""
        config = self._make_config({})   # empty — Reference.keys not configured
        query = {"$condition": {"$eq": [{"$field": "$sm#semanticId"}, {"$strVal": "urn:sem"}]}}
        with self.assertRaises(ValueError):
            convert_aasql_to_cypher(query, config=config)

    def test_convert_aasql_to_cypher_no_config_succeeds(self):
        """convert_aasql_to_cypher() without config uses convention-based names."""
        query = {"$condition": {"$eq": [{"$field": "$sm#semanticId"}, {"$strVal": "urn:sem"}]}}
        result = convert_aasql_to_cypher(query)
        self.assertIn("keys_value[0]", result)


if __name__ == "__main__":
    unittest.main()
