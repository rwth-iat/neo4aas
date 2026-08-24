"""Concepts: the CodedName hierarchy, and turning a CD into a dictionary entry."""

import pytest

from neo4aas.vocabulary.concepts import (
    coded_name_level,
    coded_name_parent,
    concept,
    concept_from_description,
)

IEC61360_CD = {
    "modelType": "ConceptDescription",
    "id": "0173-1#02-BAF016#006",
    "embeddedDataSpecifications": [{
        "dataSpecificationContent": {
            "modelType": "DataSpecificationIec61360",
            "preferredName": [{"language": "de", "text": "Betriebsspannung"},
                              {"language": "en", "text": "operating voltage"}],
            "definition": [{"language": "en", "text": "voltage during operation"}],
            "unit": "V",
            "dataType": "REAL_MEASURE",
        },
    }],
}


@pytest.mark.parametrize("coded_name,level", [
    ("27000000", 1), ("27210000", 2), ("27210100", 3), ("27210101", 4),
    ("", None), (None, None), ("2721", None), ("27abcdef", None),
])
def test_coded_name_level(coded_name, level):
    assert coded_name_level(coded_name) == level


@pytest.mark.parametrize("child,parent", [
    ("27210101", "27210100"), ("27210100", "27210000"), ("27210000", "27000000"),
    ("27000000", None), (None, None),
])
def test_coded_name_parent_is_prefix_containment(child, parent):
    """The ECLASS hierarchy is the CodedName prefix, not a parent field."""
    assert coded_name_parent(child) == parent


def test_concept_carries_the_version_stripped_base():
    """Resolution joins on the base; ECLASS bumps the version on every release."""
    row = concept("0173-1#02-BAF016#006", "eclass")
    assert row["id_base"] == "0173-1#02-BAF016"


def test_concept_from_description_prefers_iec61360():
    row = concept_from_description(IEC61360_CD)
    assert row["id"] == "0173-1#02-BAF016#006"
    assert row["preferred_name"] == "operating voltage"      # en before de
    assert row["definition"] == "voltage during operation"
    assert row["unit"] == "V" and row["data_type"] == "REAL_MEASURE"
    assert row["source"] == "aas"


def test_concept_from_description_falls_back_to_native_fields():
    row = concept_from_description({
        "modelType": "ConceptDescription", "id": "urn:x",
        "displayName": [{"language": "de", "text": "Spannung"}],
        "description": [{"language": "de", "text": "Beschreibung"}],
    })
    assert row["preferred_name"] == "Spannung"
    assert row["definition"] == "Beschreibung"


def test_language_pick_is_deterministic():
    """An order-dependent pick would make two imports write different properties."""
    swapped = dict(IEC61360_CD)
    content = swapped["embeddedDataSpecifications"][0]["dataSpecificationContent"]
    reversed_content = dict(content, preferredName=list(reversed(content["preferredName"])))
    swapped["embeddedDataSpecifications"] = [{"dataSpecificationContent": reversed_content}]
    assert concept_from_description(swapped)["preferred_name"] == \
           concept_from_description(IEC61360_CD)["preferred_name"]


def test_a_description_without_an_id_is_not_a_concept():
    assert concept_from_description({"modelType": "ConceptDescription"}) is None
    assert concept_from_description(None) is None


def test_dictionary_outranks_an_embedded_copy():
    """ECLASS wins; a CD copied into a vendor file only fills what is missing."""
    assert concept("x", "eclass")["precedence"] > concept("x", "aas")["precedence"]
