"""Convert AAS XML (v3.x) to the equivalent AAS JSON dict structure.

AAS XML and AAS JSON are isomorphic representations of the same data model.
This module converts an AAS XML <environment> document into a Python dict that
mirrors the AAS JSON format, so the existing JSON import pipeline can be reused
without modification.

No Neo4j dependency — this module is independently usable and testable.
"""
import xml.etree.ElementTree as ET
from typing import Any

AAS_NS = "https://admin-shell.io/aas/3/0"

# Elements whose direct children form a list value.
LIST_CONTAINER_TAGS: frozenset[str] = frozenset({
    "assetAdministrationShells",
    "submodels",
    "conceptDescriptions",
    "submodelElements",
    "keys",
    "displayName",
    "description",
    "extensions",
    "qualifiers",
    "embeddedDataSpecifications",
    "specificAssetIds",
    "supplementalSemanticIds",
    "preferredName",
    "shortName",
    "definition",
    "valueReferencePairs",  # valueList itself is a plain dict wrapper
    "statements",           # Entity.statements
    "annotations",          # AnnotatedRelationshipElement.annotations
    "inputVariables",
    "outputVariables",
    "inoutputVariables",
    "isCaseOf",             # ConceptDescription.isCaseOf list of Reference
    "refersTo",             # Extension.refersTo list of ModelReference
    # NOTE: "value" is handled separately below — it is a list only when all
    # children are typed AAS elements (SubmodelElementList.value), and a plain
    # dict otherwise (ReferenceElement.value contains a Reference, not an SME list).
})

# LangString variant tags — produce {"language": "...", "text": "..."} dicts.
LANG_STRING_TAGS: frozenset[str] = frozenset({
    "langStringNameType",
    "langStringTextType",
    "langStringPreferredNameTypeIec61360",
    "langStringShortNameTypeIec61360",
    "langStringDefinitionTypeIec61360",
})

# XML camelCase element tag → AAS modelType (PascalCase string injected as "modelType" key).
XML_TAG_TO_MODEL_TYPE: dict[str, str] = {
    "assetAdministrationShell": "AssetAdministrationShell",
    "submodel": "Submodel",
    "conceptDescription": "ConceptDescription",
    "property": "Property",
    "blob": "Blob",
    "file": "File",
    "operation": "Operation",
    "entity": "Entity",
    "submodelElementCollection": "SubmodelElementCollection",
    "submodelElementList": "SubmodelElementList",
    "multiLanguageProperty": "MultiLanguageProperty",
    "relationshipElement": "RelationshipElement",
    "annotatedRelationshipElement": "AnnotatedRelationshipElement",
    "capability": "Capability",
    "range": "Range",
    "referenceElement": "ReferenceElement",
    "basicEventElement": "BasicEventElement",
    "dataSpecificationIec61360": "DataSpecificationIec61360",
}

# Tags whose children are xs:boolean-typed in the AAS spec and must be converted
# to Python bool (True/False).  Only levelType qualifies — all other boolean-like
# string values (e.g. Extension.value with valueType=xs:boolean) remain strings.
_BOOLEAN_OBJECT_TAGS: frozenset[str] = frozenset({"levelType"})

# Leaf element names whose text content is xs:boolean in the AAS spec.
_BOOLEAN_LEAF_PROPS: frozenset[str] = frozenset({"orderRelevant"})

# For these parent element tags, after building the result dict unwrap the named
# child key from a 1-element list to a scalar dict.
# Handles operationVariable.value: <value> children are typed SMEs → list, but each
# operationVariable wraps exactly one SME, so the JSON form is a scalar dict.
_SINGLE_ITEM_LIST_UNWRAP: dict[str, str] = {
    "operationVariable": "value",
}


def _strip_ns(tag: str) -> str:
    """Strip any XML namespace prefix from a tag, leaving the local name.

    AAS elements are matched by local name, so this is version-agnostic across AAS
    metamodel namespaces (``3/0``, ``3/1``, …) and any non-AAS namespace — e.g.
    ``{https://admin-shell.io/aas/3/1}submodels`` → ``submodels``.
    """
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _element_to_value(element: ET.Element) -> Any:
    """Recursively convert an XML element to its AAS JSON-equivalent Python value.

    Returns None for empty complex elements (e.g. <administration/>) so callers
    can omit them. Empty string leaf values are preserved as "".
    """
    tag = _strip_ns(element.tag)
    children = list(element)
    text = element.text.strip() if element.text and element.text.strip() else ""

    # --- Leaf element (no children) ---
    if not children:
        if tag in _BOOLEAN_LEAF_PROPS:
            return text == "true"
        # Empty string leaves are preserved as ""; callers that want to omit them
        # should normalise with a filter that removes empty strings.
        return text  # may be ""

    # --- LevelType: children are xs:boolean — convert "true"/"false" → bool ---
    if tag in _BOOLEAN_OBJECT_TAGS:
        return {_strip_ns(c.tag): (c.text or "").strip() == "true" for c in children}

    # --- LangString: {"language": "...", "text": "..."} ---
    if tag in LANG_STRING_TAGS:
        return {_strip_ns(c.tag): (c.text or "").strip() for c in children}

    # --- List container: children form a list ---
    if tag in LIST_CONTAINER_TAGS:
        items = []
        for child in children:
            val = _element_to_value(child)
            if val is not None and val != "":
                items.append(val)
        return items

    # --- value: context-sensitive handling ---
    # SubmodelElementList.value  → list of typed SME children (all in XML_TAG_TO_MODEL_TYPE)
    # MultiLanguageProperty.value → list of LangString children (all in LANG_STRING_TAGS)
    # ReferenceElement.value     → Reference dict (children are <type>, <keys>)
    # Property.value / scalar    → already handled by leaf branch above (no children)
    if tag == "value" and children:
        first_child_tag = _strip_ns(children[0].tag)
        if first_child_tag in XML_TAG_TO_MODEL_TYPE or first_child_tag in LANG_STRING_TAGS:
            return [_element_to_value(c) for c in children]
        # Otherwise fall through to general dict handling (e.g. ReferenceElement.value)

    # --- Single typed child unwrap ---
    # When a wrapper element (e.g. <dataSpecificationContent>) contains exactly
    # one child whose tag is a known AAS typed element, unwrap to the child dict.
    if len(children) == 1 and _strip_ns(children[0].tag) in XML_TAG_TO_MODEL_TYPE:
        return _element_to_value(children[0])

    # --- General dict element ---
    result: dict[str, Any] = {}
    if tag in XML_TAG_TO_MODEL_TYPE:
        result["modelType"] = XML_TAG_TO_MODEL_TYPE[tag]

    for child in children:
        child_tag = _strip_ns(child.tag)
        val = _element_to_value(child)
        if val is not None and val != "":
            result[child_tag] = val

    # Post-process: unwrap 1-element list → scalar for known parent/key pairs.
    # Handles operationVariable.value (list of one typed SME → scalar SME dict).
    unwrap_key = _SINGLE_ITEM_LIST_UNWRAP.get(tag)
    if unwrap_key and isinstance(result.get(unwrap_key), list) and len(result[unwrap_key]) == 1:
        result[unwrap_key] = result[unwrap_key][0]

    return result if result else None


def xml_to_aas_json(source: str | bytes) -> dict:
    """Parse an AAS XML environment and return the AAS JSON-equivalent dict.

    Args:
        source: File path (str) or raw XML bytes.

    Returns:
        Dict with top-level AAS keys, e.g.:
        {
            "assetAdministrationShells": [...],
            "submodels": [...],
            "conceptDescriptions": [...],
        }
    """
    if isinstance(source, bytes):
        root = ET.fromstring(source)
    else:
        root = ET.parse(source).getroot()

    result: dict[str, Any] = {}
    for child in root:
        tag = _strip_ns(child.tag)
        val = _element_to_value(child)
        if val is not None and val != "":
            result[tag] = val
    return result
