"""Unit tests for xml_to_aas_json (no Neo4j).

Focus: namespace handling must be version-agnostic across AAS metamodel namespaces
(3/0, 3/1, …) and tolerant of an explicit element prefix.
"""

from aas_mapping.aas_neo4j_adapter.xmlification.xml_to_json import xml_to_aas_json

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
