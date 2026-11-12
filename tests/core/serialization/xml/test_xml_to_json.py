"""Unit tests for xml_to_aas_json (no Neo4j).

Focus: namespace handling must be version-agnostic across AAS metamodel namespaces
(3/0, 3/1, …) and tolerant of an explicit element prefix.
"""

from neo4aas.core.serialization.xml.xml_to_json import xml_to_aas_json

# Default-namespace 3/0 (as ABB/Buerkert export it).
_XML_30 = (
    '<environment xmlns="https://admin-shell.io/aas/3/0">'
    "<assetAdministrationShells><assetAdministrationShell>"
    "<idShort>Shell30</idShort><id>urn:shell/30</id>"
    "</assetAdministrationShell></assetAdministrationShells>"
    "<submodels><submodel><idShort>Sm30</idShort><id>urn:sm/30</id></submodel></submodels>"
    "</environment>"
)

# Prefixed namespace 3/1 (as SICK export it).
_XML_31 = (
    "<aas:environment xmlns:aas=\"https://admin-shell.io/aas/3/1\">"
    "<aas:assetAdministrationShells><aas:assetAdministrationShell>"
    "<aas:idShort>Shell31</aas:idShort><aas:id>urn:shell/31</aas:id>"
    "</aas:assetAdministrationShell></aas:assetAdministrationShells>"
    "<aas:submodels><aas:submodel><aas:idShort>Sm31</aas:idShort><aas:id>urn:sm/31</aas:id></aas:submodel></aas:submodels>"
    "</aas:environment>"
)


def test_parses_default_ns_30():
    d = xml_to_aas_json(_XML_30.encode())
    assert [s["id"] for s in d["assetAdministrationShells"]] == ["urn:shell/30"]
    assert d["assetAdministrationShells"][0]["modelType"] == "AssetAdministrationShell"
    assert [s["id"] for s in d["submodels"]] == ["urn:sm/30"]


def test_parses_prefixed_ns_31():
    """3/1 + an explicit prefix must yield the same local-name keys, not {ns}tag keys."""
    d = xml_to_aas_json(_XML_31.encode())
    assert "assetAdministrationShells" in d  # local name, namespace stripped
    assert [s["id"] for s in d["assetAdministrationShells"]] == ["urn:shell/31"]
    assert d["assetAdministrationShells"][0]["modelType"] == "AssetAdministrationShell"
    assert [s["id"] for s in d["submodels"]] == ["urn:sm/31"]


def _submodel(elements: str) -> dict:
    xml = (
        '<environment xmlns="https://admin-shell.io/aas/3/0"><submodels><submodel>'
        f"<id>urn:sm</id>{elements}</submodel></submodels></environment>"
    )
    return xml_to_aas_json(xml.encode())["submodels"][0]


def test_keeps_empty_values_and_objects():
    """Empty elements become what their JSON form holds, not nothing."""
    sm = _submodel(
        "<administration/><submodelElements>"
        "<property><idShort>p</idShort><valueType>xs:string</valueType><value/></property>"
        "<submodelElementList><idShort>l</idShort><typeValueListElement>Entity"
        "</typeValueListElement><value><entity/></value></submodelElementList>"
        "</submodelElements>"
    )
    assert sm["administration"] == {}
    assert sm["submodelElements"][0]["value"] == ""
    assert sm["submodelElements"][1]["value"] == [{"modelType": "Entity"}]


def test_keeps_surrounding_whitespace_of_values():
    sm = _submodel("<idShort>s</idShort><category> PARAMETER </category>")
    assert sm["id"] == "urn:sm"
    assert sm["category"] == " PARAMETER "


def test_converts_data_specification_uom():
    sm = _submodel(
        "<embeddedDataSpecifications><embeddedDataSpecification><dataSpecification>"
        "<type>ExternalReference</type><keys><key><type>GlobalReference</type>"
        "<value>urn:ds</value></key></keys></dataSpecification><dataSpecificationContent>"
        "<dataSpecificationUom><preferredName><langStringUom><language>en</language>"
        "<text>metre</text></langStringUom></preferredName><symbol>m</symbol>"
        "</dataSpecificationUom></dataSpecificationContent></embeddedDataSpecification>"
        "</embeddedDataSpecifications>"
    )
    content = sm["embeddedDataSpecifications"][0]["dataSpecificationContent"]
    assert content == {
        "modelType": "DataSpecificationUom",
        "preferredName": [{"language": "en", "text": "metre"}],
        "symbol": "m",
    }
