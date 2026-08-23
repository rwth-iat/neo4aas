"""IEC CDD: the second dictionary source.

The semantics that matter are pinned against hand-written records, because they
are decisions about *this* export's defects — relative IRDIs, one IRDI published
several times — and a corpus file cannot say which behaviour was intended. The
last tests then run the real export, since surviving real data is the job.
"""

import json

import pytest

from neo4aas.vocabulary.iec_cdd import (
    class_broader_pairs,
    concepts_from_iec_cdd,
    parse_iec_cdd,
    property_class_pairs,
)
from tests.paths import CORPUS_IEC_CDD_DIR


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


PROPERTY = {
    "irdi": "0112/2///61360_4#AAF120#001",
    "code": "0112/2///61360_4#AAF120",
    "_type": "property",
    "preferred_name_en": "comb depth",
    "definition_en": "The minimum comb depth (in dB) of a comb filter.",
    "unit": "dB",
    "data_type": "LEVEL(MIN) OF INT_MEASURE_TYPE",
    "revision": "01",
    "applicable_class_irdis": ["0112/2///61360_4#AAA056"],
}

CLASS = {
    "irdi": "0112/2///61360_4#AAA622#001",
    "code": "0112/2///61360_4#AAA622",
    "_type": "class",
    "preferred_name_en": "humidity",
    "definition_en": "degree of wetness of the atmosphere",
    "superclass_irdi": "0112/2///61360_4#AAA650",
    "higher_level_classes": [{"irdi": "0112/2///61360_4#AAA000", "name": "components"}],
    "properties_own": [{"irdi": "0112/2///61360_4#AAF893", "name": "relative humidity"}],
    "properties_inherited": [{"irdi": "0112/2///61360_4#AAZ999", "name": "inherited"}],
    "revision": "04",
}

UNIT = {
    "irdi": "0112/2///62720#UAA033#001",
    "code": "0112/2///62720#UAA033",
    "_type": "unit",
    "preferred_name_en": "degree Celsius",
    "short_name_en": "°C",
    "revision": "01",
}


@pytest.fixture
def parsed(tmp_path):
    return parse_iec_cdd([write_jsonl(tmp_path / "d.jsonl", [PROPERTY, CLASS, UNIT])])


def rows_by_irdi(result):
    return {row["irdi"]: row for row in concepts_from_iec_cdd(result)}


# -- identity ------------------------------------------------------------------


def test_every_record_type_becomes_a_concept_of_its_own_kind(parsed):
    rows = rows_by_irdi(parsed)
    assert {r["kind"] for r in rows.values()} == {"property", "class", "unit"}
    assert all(r["source"] == "iec_cdd" for r in rows.values())


def test_concepts_join_on_the_version_stripped_base(parsed):
    """``irdi_base`` is the join key, and the export's own ``code`` agrees with it.

    The two are computed independently — ``code`` by IEC, the base by
    :func:`neo4aas.core.utils.irdi_base` — so their agreement is what says a
    ``0112/2///…`` identifier is version-stripped the way ECLASS's is.
    """
    for row in concepts_from_iec_cdd(parsed):
        assert row["irdi_base"] == parsed.records[row["irdi"]]["code"]


def test_definition_and_name_come_from_the_english_fields(parsed):
    row = rows_by_irdi(parsed)[PROPERTY["irdi"]]
    assert row["preferred_name"] == "comb depth"
    assert row["definition"].startswith("The minimum comb depth")
    assert row["data_type"] == "LEVEL(MIN) OF INT_MEASURE_TYPE"


def test_iec_cdd_concepts_have_no_coded_name(parsed):
    """CDD hierarchy is an explicit ``superclass_irdi``, not ECLASS's positional
    CodedName — so the prefix-hierarchy derivation must find nothing to do."""
    assert all(row["coded_name"] is None and row["level"] is None
               for row in concepts_from_iec_cdd(parsed))


# -- units ---------------------------------------------------------------------


def test_unit_symbol_is_resolved_through_unit_irdi_when_no_symbol_is_given(tmp_path):
    silent = dict(PROPERTY, unit=None, unit_irdi="0112/2///62720#UAA033#001")
    result = parse_iec_cdd([write_jsonl(tmp_path / "d.jsonl", [silent, UNIT])])
    assert rows_by_irdi(result)[PROPERTY["irdi"]]["unit"] == "°C"


def test_unit_irdi_is_matched_version_agnostically(tmp_path):
    """The referenced version need not be the one the export ships."""
    silent = dict(PROPERTY, unit=None, unit_irdi="0112/2///62720#UAA033#007")
    result = parse_iec_cdd([write_jsonl(tmp_path / "d.jsonl", [silent, UNIT])])
    assert rows_by_irdi(result)[PROPERTY["irdi"]]["unit"] == "°C"


# -- hierarchy -----------------------------------------------------------------


def test_class_hierarchy_is_the_direct_superclass_only(parsed):
    """``higher_level_classes`` is the whole ancestor chain; taking it would make
    every ancestor a direct parent and flatten the hierarchy it describes."""
    assert list(class_broader_pairs(parsed)) == [
        ("0112/2///61360_4#AAA622", "0112/2///61360_4#AAA650")]


def test_property_class_pairs_come_from_both_directions(parsed):
    """A property names its classes and a class names its properties; the export
    fills the two inconsistently, so both are read."""
    pairs = set(property_class_pairs(parsed))
    assert ("0112/2///61360_4#AAF120", "0112/2///61360_4#AAA056") in pairs
    assert ("0112/2///61360_4#AAF893", "0112/2///61360_4#AAA622") in pairs


def test_inherited_properties_are_not_attached_to_the_inheriting_class(parsed):
    """The class hierarchy already carries them; attaching them again would give a
    deep class every ancestor's properties as its own."""
    assert all(p != "0112/2///61360_4#AAZ999" for p, _ in property_class_pairs(parsed))


# -- the export's defects ------------------------------------------------------


def test_a_relative_irdi_is_skipped_and_counted_not_repaired(tmp_path):
    """1 547 properties are scraped without their ICD prefix (``AAE930#001``).

    Such a value cannot join anything, and the prefix could only be *guessed* from
    ``published_in`` — inventing an identifier is worse than dropping a record that
    1 511 times over already exists in full form elsewhere in the export.
    """
    relative = dict(PROPERTY, irdi="AAE930#001", code="AAE930")
    result = parse_iec_cdd([write_jsonl(tmp_path / "d.jsonl", [relative, CLASS])])
    assert result.skipped_relative == 1
    assert "AAE930#001" not in result.records


def test_one_irdi_published_several_times_keeps_the_highest_revision(tmp_path):
    """6 445 IRDIs appear more than once with different content, differing in
    ``revision`` — the CDD revises inside a version. The pick must not depend on
    file order, or two imports of the same export would disagree."""
    old = dict(PROPERTY, revision="01", preferred_name_en="old name")
    new = dict(PROPERTY, revision="03", preferred_name_en="new name")
    forward = parse_iec_cdd([write_jsonl(tmp_path / "a.jsonl", [old, new])])
    backward = parse_iec_cdd([write_jsonl(tmp_path / "b.jsonl", [new, old])])
    assert forward.records[PROPERTY["irdi"]]["preferred_name_en"] == "new name"
    assert backward.records[PROPERTY["irdi"]]["preferred_name_en"] == "new name"
    assert forward.superseded_records == backward.superseded_records == 1


def test_a_tie_on_revision_is_broken_deterministically(tmp_path):
    a = dict(PROPERTY, revision="02", unid="bbb", preferred_name_en="b")
    b = dict(PROPERTY, revision="02", unid="aaa", preferred_name_en="a")
    forward = parse_iec_cdd([write_jsonl(tmp_path / "a.jsonl", [a, b])])
    backward = parse_iec_cdd([write_jsonl(tmp_path / "b.jsonl", [b, a])])
    assert (forward.records[PROPERTY["irdi"]]["preferred_name_en"]
            == backward.records[PROPERTY["irdi"]]["preferred_name_en"])


def test_superseded_is_a_version_state_not_a_deprecation(tmp_path):
    """Half the export is ``Superseded`` — that marks an older version of a live
    concept, not a withdrawn one. Only the explicit flag deprecates."""
    superseded = dict(PROPERTY, status_level="Superseded")
    withdrawn = dict(CLASS, is_deprecated=True)
    result = parse_iec_cdd([write_jsonl(tmp_path / "d.jsonl", [superseded, withdrawn])])
    rows = rows_by_irdi(result)
    assert rows[PROPERTY["irdi"]]["deprecated"] is False
    assert rows[CLASS["irdi"]]["deprecated"] is True


def test_a_line_that_will_not_parse_is_reported_not_raised(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps(PROPERTY) + "\n{ not json\n" + json.dumps(CLASS) + "\n")
    result = parse_iec_cdd([path])
    assert len(result.records) == 2
    assert len(result.failures) == 1
    assert "d.jsonl:2" in result.failures[0][0]


def test_a_record_without_an_irdi_is_skipped(tmp_path):
    """2 enumeration entries in the export carry ``_error`` and no identifier."""
    result = parse_iec_cdd([write_jsonl(tmp_path / "d.jsonl",
                                        [{"_type": "unit", "_error": "timeout"}, CLASS])])
    assert list(result.records) == [CLASS["irdi"]]


# -- the real export -----------------------------------------------------------


@pytest.fixture(scope="session")
def corpus_cdd():
    if not CORPUS_IEC_CDD_DIR.is_dir():
        pytest.skip(f"IEC CDD not available at {CORPUS_IEC_CDD_DIR}")
    return CORPUS_IEC_CDD_DIR


def test_the_real_export_parses_into_every_kind(corpus_cdd):
    result = parse_iec_cdd([corpus_cdd / "iec63508.jsonl"])
    kinds = {row["kind"] for row in concepts_from_iec_cdd(result)}
    assert kinds == {"class", "property", "enumeration", "terminology"}
    assert not result.failures


def test_a_directory_reads_every_dictionary_in_it(corpus_cdd):
    result = parse_iec_cdd([corpus_cdd])
    assert len(result.records) > 20_000
    assert result.skipped_relative > 1_000, "the 61987 export ships relative IRDIs"
    assert not result.failures
