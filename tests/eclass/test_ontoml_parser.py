"""Unit tests for the ECLASS OntoML parser (no Neo4j).

Feeds a minimal-but-realistic ECLASS XML 5.0 dictionary document (the same
element shapes as the real ``ECLASS15_0_BASIC_DE_SG_*.xml`` segment files) and
checks classes/properties are extracted into typed models.
"""

from neo4aas.eclass.ontoml_parser import EclassOntomlParser

# One class (with a superclass) and two properties (one a measure with a unit,
# one deprecated), wrapped in the real ECLASS dictionary/ontoml namespaces.
SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<dic:eclass_dictionary
    xmlns:dic="urn:eclass:xml-schema:dictionary:5.0"
    xmlns:ontoml="urn:iso:std:iso:is:13584:-32:ed-1:tech:xml-schema:ontoml"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <ontoml:ontoml>
    <ontoml:class xsi:type="ontoml:CATEGORIZATION_CLASS_Type" id="0173-1#01-AFW236#006">
      <preferred_name>
        <label language_code="de" country_code="DE">Entwicklung (Dienstleistung)</label>
      </preferred_name>
      <definition>
        <text language_code="de" country_code="DE">Sachgebiet umfasst Services.</text>
      </definition>
      <its_superclass class_ref="0173-1#01-AAA002#001"/>
      <described_by>
        <property property_ref="0173-1#02-BAD847#003" order_number="12" is_deprecated="true"/>
        <property property_ref="0173-1#02-AAH880#003" order_number="3"/>
      </described_by>
      <hierarchical_position>13000000</hierarchical_position>
    </ontoml:class>
    <ontoml:property xsi:type="ontoml:NON_DEPENDENT_P_DET_Type" id="0173-1#02-BAD847#003">
      <preferred_name>
        <label language_code="de" country_code="DE">Hersteller-Artikelnummer</label>
      </preferred_name>
      <short_name>
        <label language_code="de" country_code="DE">MAN_PROD_NUM</label>
      </short_name>
      <definition>
        <text language_code="de" country_code="DE">eindeutiger Produktschluessel</text>
      </definition>
      <domain xsi:type="ontoml:TRANSLATABLE_STRING_TYPE_Type"/>
      <is_deprecated>true</is_deprecated>
    </ontoml:property>
    <ontoml:property xsi:type="ontoml:LEVEL_P_DET_Type" id="0173-1#02-AAH880#003">
      <preferred_name>
        <label language_code="de" country_code="DE">Hoehe</label>
      </preferred_name>
      <definition>
        <text language_code="de" country_code="DE">Hoehe des Objekts</text>
      </definition>
      <domain xsi:type="ontoml:REAL_MEASURE_TYPE_Type">
        <unit unit_ref="0173-1#05-AAA480#002"/>
      </domain>
    </ontoml:property>
  </ontoml:ontoml>
</dic:eclass_dictionary>
"""


def test_parse_string_extracts_classes_and_properties():
    result = EclassOntomlParser().parse_string(SAMPLE_XML)

    assert len(result.classes) == 1
    assert len(result.properties) == 2


def test_class_fields():
    cls = EclassOntomlParser().parse_string(SAMPLE_XML).classes[0]

    assert cls.irdi == "0173-1#01-AFW236#006"
    assert cls.superclass_irdi == "0173-1#01-AAA002#001"
    assert cls.hierarchical_position == "13000000"
    assert cls.preferred_name[0].language == "de"
    assert cls.preferred_name[0].text == "Entwicklung (Dienstleistung)"
    assert cls.definition[0].text == "Sachgebiet umfasst Services."
    assert cls.is_deprecated is False


def test_class_property_refs_from_described_by():
    """ADVANCED class→property assignment comes from <described_by>, in order."""
    cls = EclassOntomlParser().parse_string(SAMPLE_XML).classes[0]

    assert cls.property_refs == ["0173-1#02-BAD847#003", "0173-1#02-AAH880#003"]


def test_property_fields_and_data_type():
    props = {p.irdi: p for p in EclassOntomlParser().parse_string(SAMPLE_XML).properties}

    string_prop = props["0173-1#02-BAD847#003"]
    assert string_prop.preferred_name[0].text == "Hersteller-Artikelnummer"
    assert string_prop.short_name[0].text == "MAN_PROD_NUM"
    assert string_prop.data_type == "TRANSLATABLE_STRING_TYPE_Type"
    assert string_prop.unit_irdi is None
    assert string_prop.is_deprecated is True

    measure_prop = props["0173-1#02-AAH880#003"]
    assert measure_prop.data_type == "REAL_MEASURE_TYPE_Type"
    assert measure_prop.unit_irdi == "0173-1#05-AAA480#002"
    assert measure_prop.is_deprecated is False


def test_parse_file_matches_parse_string(tmp_path):
    """parse_file streams the document (iterparse) instead of building a full DOM.

    A real ECLASS segment is up to ~541 MB of XML and the DOM costs ~7x the file size
    (measured: 89 MB segment -> 638 MB RSS), so the big segments could not be parsed in a
    memory-capped container. Streaming must produce exactly the same models — including
    the nested <described_by><property property_ref=.../> links, which live *inside* a
    class element and are the thing a naive "clear every element" loop destroys.
    """
    path = tmp_path / "segment_SG_13.xml"
    path.write_text(SAMPLE_XML, encoding="utf-8")

    streamed = EclassOntomlParser().parse_file(path)
    in_memory = EclassOntomlParser().parse_string(SAMPLE_XML)

    assert streamed.classes == in_memory.classes
    assert streamed.properties == in_memory.properties
    assert streamed.classes[0].property_refs == [
        "0173-1#02-BAD847#003", "0173-1#02-AAH880#003",
    ]


def test_parse_file_keeps_peak_memory_flat(tmp_path):
    """Peak allocation must not scale with the number of definitions in the file."""
    import tracemalloc

    head, _, tail = SAMPLE_XML.partition("<ontoml:ontoml>")
    body, _, close = tail.rpartition("</ontoml:ontoml>")

    def write(path, repeats):
        # Each repeat gets unique ids so nothing is deduplicated away.
        chunks = [body.replace("#006", f"#{i:03d}").replace("#003", f"#{i:03d}")
                  for i in range(repeats)]
        path.write_text(head + "<ontoml:ontoml>" + "".join(chunks) + "</ontoml:ontoml>" + close,
                        encoding="utf-8")

    small, big = tmp_path / "small_SG_13.xml", tmp_path / "big_SG_13.xml"
    write(small, 20)
    write(big, 400)

    peaks = []
    for path in (small, big):
        tracemalloc.start()
        EclassOntomlParser().parse_file(path)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()

    small_peak, big_peak = peaks
    # 20x the definitions: a DOM parse scales with the document, streaming does not.
    # The parsed models themselves are retained, so allow generous headroom.
    assert big_peak < small_peak * 8, f"peak grew {big_peak / small_peak:.1f}x with document size"
