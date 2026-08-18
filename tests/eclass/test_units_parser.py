"""Unit tests for ECLASS UnitsML parsing (no Neo4j)."""

from neo4aas.eclass.ontoml_parser import EclassOntomlParser

UNITS_XML = """<?xml version='1.0' encoding='UTF-8'?>
<unt:eclass_units
    xmlns:unt="urn:eclass:xml-schema:units:5.0"
    xmlns:unitsml="urn:oasis:names:tc:unitsml:schema:xsd:UnitsMLSchema-1.0">
  <unitsml:UnitSet>
    <unt:eClassUnit xml:id="id0173-1x05-AAA589x003">
      <unitsml:UnitName xml:lang="de-DE">Imp/kVAh</unitsml:UnitName>
      <unitsml:CodeListValue unitCodeValue="0173-1#05-AAA589#003" codeListName="IRDI"/>
      <unitsml:CodeListValue unitCodeValue="Imp/kVAh" codeListName="DIN code"/>
      <unt:preferred_name>
        <label language_code="de" country_code="DE">Imp je Kilovoltamperestunde</label>
      </unt:preferred_name>
      <unt:short_name>
        <label language_code="de" country_code="DE">Imp/kVAh</label>
      </unt:short_name>
    </unt:eClassUnit>
    <unt:eClassUnit xml:id="id0173-1x05-AAA480x002">
      <unitsml:CodeListValue unitCodeValue="0173-1#05-AAA480#002" codeListName="IRDI"/>
      <unt:preferred_name>
        <label language_code="de" country_code="DE">Millimeter</label>
      </unt:preferred_name>
      <unt:short_name>
        <label language_code="de" country_code="DE">mm</label>
      </unt:short_name>
    </unt:eClassUnit>
  </unitsml:UnitSet>
</unt:eclass_units>
"""


def test_parse_units_string():
    units = EclassOntomlParser().parse_units_string(UNITS_XML)
    by_irdi = {u.irdi: u for u in units}

    assert set(by_irdi) == {"0173-1#05-AAA589#003", "0173-1#05-AAA480#002"}

    mm = by_irdi["0173-1#05-AAA480#002"]
    assert mm.preferred_name == "Millimeter"
    assert mm.short_name == "mm"
