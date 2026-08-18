"""Unit tests for the ECLASS -> ConceptDescription writer (no Neo4j).

CDs use the ConceptDescription's native fields — ``display_name`` (preferred
name) and ``description`` (definition) — with class hierarchy on ``isCaseOf``.
No embedded DataSpecificationIEC61360; property->unit is a downstream graph edge.
"""

import basyx.aas.model as model

from neo4aas.eclass.models import EclassClass, EclassProperty, EclassUnit, LangText
from neo4aas.eclass.spec_cd_writer import SpecCdWriter


def test_property_to_cd():
    prop = EclassProperty(
        irdi="0173-1#02-AAH880#003",
        preferred_name=[LangText("de", "Hoehe")],
        short_name=[LangText("de", "H")],
        definition=[LangText("de", "Hoehe des Objekts")],
        data_type="REAL_MEASURE_TYPE_Type",
        unit_irdi="0173-1#05-AAA480#002",
    )
    cd = SpecCdWriter().property_to_cd(prop)

    assert isinstance(cd, model.ConceptDescription)
    assert cd.id == "0173-1#02-AAH880#003"
    assert cd.id_short is None  # properties carry no idShort
    assert cd.display_name["de"] == "Hoehe"
    assert cd.description["de"] == "Hoehe des Objekts"
    assert not cd.embedded_data_specifications


def test_class_to_cd_hierarchy_via_is_case_of():
    cls = EclassClass(
        irdi="0173-1#01-AFW236#006",
        preferred_name=[LangText("de", "Entwicklung")],
        definition=[LangText("de", "Sachgebiet")],
        superclass_irdi="0173-1#01-AAA002#001",
        hierarchical_position="27200601",
    )
    cd = SpecCdWriter().class_to_cd(cls)

    assert cd.id == "0173-1#01-AFW236#006"
    assert cd.id_short == "eclass_27200601"
    refs = {r.key[0].value for r in cd.is_case_of}
    assert "0173-1#01-AAA002#001" in refs
    assert cd.display_name["de"] == "Entwicklung"
    assert cd.description["de"] == "Sachgebiet"


def test_class_no_hierarchical_position_has_no_id_short():
    cls = EclassClass(irdi="0173-1#01-AFW236#006", preferred_name=[LangText("de", "X")])
    cd = SpecCdWriter().class_to_cd(cls)
    assert cd.id_short is None
    assert not cd.is_case_of  # basyx normalizes None to an empty set


def test_unit_to_cd():
    unit = EclassUnit(irdi="0173-1#05-AAA480#002", short_name="mm", preferred_name="Millimeter")
    cd = SpecCdWriter().unit_to_cd(unit)

    assert cd.id == "0173-1#05-AAA480#002"
    assert cd.id_short == "mm"
    assert cd.display_name["de"] == "Millimeter"


def test_unit_id_short_sanitized_to_none_for_symbols():
    unit = EclassUnit(irdi="0173-1#05-AAA127#004", short_name="m/s", preferred_name="Meter pro Sekunde")
    cd = SpecCdWriter().unit_to_cd(unit)
    assert cd.id_short is None  # 'm/s' is not a valid idShort
    assert cd.display_name["de"] == "Meter pro Sekunde"


def test_string_constraints_truncated():
    """display_name >128 chars and description >1023 chars are truncated to fit."""
    prop = EclassProperty(
        irdi="0173-1#02-XXX000#001",
        preferred_name=[LangText("de", "x" * 300)],
        definition=[LangText("de", "z" * 2000)],
    )
    cd = SpecCdWriter().property_to_cd(prop)
    assert len(cd.display_name["de"]) == 64
    assert len(cd.description["de"]) == 1023


def test_display_name_falls_back_to_irdi():
    prop = EclassProperty(irdi="0173-1#02-XXX001#001")
    cd = SpecCdWriter().property_to_cd(prop)
    assert cd.display_name["en"] == "0173-1#02-XXX001#001"
    assert cd.description is None
