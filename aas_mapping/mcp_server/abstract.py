"""Build an abstract (Template-kind) submodel from a list of AAS JSON instances.

The result is the structural union of all instances: every element path that
appears in at least one instance is included, instance-specific values are
stripped, and kind is set to "Template".
"""

from __future__ import annotations

import copy
from typing import Any

from aas_mapping.aas_neo4j_adapter.utils import NEO4J_INTERNAL_NODE_KEYS

# Model types whose "value" is an instance value (scalar/blob/ref), not children.
_LEAF_TYPES = frozenset(
    {
        "Property",
        "MultiLanguageProperty",
        "Range",
        "Blob",
        "File",
        "ReferenceElement",
        "RelationshipElement",
        "AnnotatedRelationshipElement",
    }
)

# Model types whose "value" or "submodelElements" is a list of child elements.
_CONTAINER_TYPES = frozenset({"Submodel", "SubmodelElementCollection"})


def to_template(element: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied, value-stripped Template version of *element*.

    Instances reaching here are normally already stripped of Neo4j-internal keys
    (``get_identifiables_by_type`` strips before calling), but we filter again with
    the canonical ``NEO4J_INTERNAL_NODE_KEYS`` so the helper is also correct for any
    caller that passes raw exported dicts.
    """
    result: dict[str, Any] = {
        k: v for k, v in element.items() if k not in NEO4J_INTERNAL_NODE_KEYS
    }
    model_type = element.get("modelType", "")

    if model_type == "Submodel":
        result["kind"] = "Template"
        id_short = element.get("idShort", "unknown")
        result["id"] = f"abstract:{id_short}"
        result.pop("administration", None)
        children = element.get("submodelElements", [])
        result["submodelElements"] = [to_template(c) for c in children]

    elif model_type == "SubmodelElementCollection":
        children = element.get("value", [])
        result["value"] = [to_template(c) for c in children]

    elif model_type == "SubmodelElementList":
        # Keep one representative child to show element type.
        children = element.get("value", [])
        result["value"] = [to_template(children[0])] if children else []

    elif model_type in _LEAF_TYPES:
        result.pop("value", None)
        result.pop("min", None)
        result.pop("max", None)

    return result


def _children_key(model_type: str) -> str:
    return "submodelElements" if model_type == "Submodel" else "value"


def _merge_structure(base: dict[str, Any], other: dict[str, Any]) -> None:
    """Merge missing element paths from *other* into *base* in-place."""
    model_type = base.get("modelType", "")
    if model_type not in _CONTAINER_TYPES:
        return

    key = _children_key(model_type)
    base_children: list[dict] = base.setdefault(key, [])
    other_children: list[dict] = other.get(key, [])

    base_index = {c["idShort"]: c for c in base_children if "idShort" in c}

    for other_child in other_children:
        id_short = other_child.get("idShort")
        if id_short is None:
            continue
        if id_short not in base_index:
            # Element exists in other but not in base — add it.
            base_children.append(copy.deepcopy(other_child))
            base_index[id_short] = base_children[-1]
        else:
            # Both have this element — recurse to merge nested structure.
            _merge_structure(base_index[id_short], other_child)


def build_abstract_submodel(instances: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a Template submodel that is the structural union of *instances*.

    Each instance must be a full AAS JSON submodel dict as returned by
    ``AASNeo4JClient.get_identifiable()``.

    Raises ``ValueError`` if *instances* is empty.
    """
    if not instances:
        raise ValueError("No submodel instances provided.")

    abstract = to_template(instances[0])
    for other in instances[1:]:
        _merge_structure(abstract, to_template(other))

    return abstract
