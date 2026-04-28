from typing import Tuple
import re

from aas_mapping.aas_neo4j_adapter.querification.ast_nodes import *


def _convert_sme(root: str, mapping: dict[str, int]) -> Tuple[str, str]:
    """
    Convert a SubmodelElement root string to a Cypher match part and last root identifier.

    The `$sme` root indicates a path starting from a Submodel under which submodelElements
    are traversed. Path segments may contain list indexing using square brackets, for example:
        "$sme.myElement[0].subElement"

    Returned tuple:
        (match_part, last_root_identifier)

    - match_part is the Cypher MATCH fragment representing the traversal from Submodel
      to nested SubmodelElements and any list-indexed edges.
    - last_root_identifier is the identifier name of the deepest SubmodelElement node used
      for attribute property lookups (e.g., "sme0", "sme1", ...). If no explicit idShort was
      available, "sme" is used as the last root.

    Raises:
        ValueError: if `root` does not contain the `$sme` prefix.
    """
    if "$sme" not in root:
        raise ValueError(f"Root does not contain $sme: {root}")
    match_part: str = "(sm:Submodel)-[:submodelElements]->"
    last_root: str = ""
    if "sme" in mapping:
        depth = mapping["sme"]
    else:
        mapping["sme"] = 0
        depth = 0
    for part in root.split(".")[1:]:
        if "[" in part:
            for p in part.split("["):
                if "]" not in p:
                    if depth == 0:
                        match_part += f"(sme{depth}:SubmodelElement {{idShort: '{p}'}})"
                    else:
                        match_part += f"-[:value]->(sme{depth}:SubmodelElement {{idShort: '{p}'}})"
                elif len(p) > 1:
                    # FIXME: take a look here: why we have a list_index for SubmodelELements?
                    match_part += f"-[:value {{list_index: {p[:-1]}}}]->(sme{depth}:SubmodelElement)"
                else:
                    match_part += f"-[:value]->(sme{depth}:SubmodelElement)"
                depth += 1
        else:
            if depth == 0:
                match_part += f"(sme{depth}:SubmodelElement {{idShort: '{part}'}})"
            else:
                match_part += f"-[:value]->(sme{depth}:SubmodelElement {{idShort: '{part}'}})"
            last_root = f"sme{depth}"
            depth += 1
    if last_root != "":
        mapping["sme"] = depth
        return match_part, last_root
    match_part += f"(sme{depth}: SubmodelElement)"
    last_root = f"sme{depth}"
    mapping["sme"] = depth + 1
    return match_part, last_root


def _convert_root(root: str, mapping: dict[str, int]) -> Tuple[str, str]:
    """
    Convert the root part of a field to a Cypher match part and last root identifier.

    Supported roots:
      - "$aas" -> AssetAdministrationShell node
      - "$sm"  -> Submodel node
      - "$cd"  -> ConceptDescription node
      - otherwise delegated to `_convert_sme` to handle SubmodelElement paths

    Returns:
        (match_part, last_root): match_part is a Cypher node pattern string,
                                 last_root is the identifier used for subsequent attribute access.
    """
    match_part: str = ""
    last_root: str = ""
    match root:
        case "$aas":
            match_part += "(aas:AssetAdministrationShell)"
            last_root = "aas"
        case "$sm":
            match_part += "(sm:Submodel)"
            last_root = "sm"
        case "$cd":
            match_part += "(cd:ConceptDescription)"
            last_root = "cd"
        case "$aasdesc":
            match_part += "(aasdesc:AssetAdministrationShellDescriptor)"
            last_root = "aasdesc"
        case "$smdesc":
            match_part += "(smdesc:SubmodelDescriptor)"
            last_root = "smdesc"
        case _:
            match_part, last_root = _convert_sme(root, mapping)
    return match_part, last_root


def _convert_attribute_elements(attribute: str, last_root: str, mapping: dict[str, int]) -> Tuple[str, str, bool]:
    """
    Convert attribute elements of a field to Cypher WHERE expression and MATCH addition.

    The `attribute` string is a dotted path of attributes relative to `last_root`.
    This function generates:
      - where_part: fragment referencing properties for WHERE clauses
      - match_part: any additional traversals required to reach nested nodes
      - isList: boolean indicating whether the resolved attribute is a list-like value

    Examples of mapping rules:
      - "id" -> "{last_root}.id"
      - "name" -> "{last_root}.name"
      - "assetInformation" -> adds a node traversal "-[:assetInformation]->(assetInformation:AssetInformation)"
      - "keys[0]" or "keys_value[0]" -> map to positional access inside reference keys
      - "language" within a MultiLanguageProperty -> uses "{last_root}.value_language" and marks `isList` True

    Returns:
        (where_part, match_part, isList)
    """
    match_part: str = ""
    where_part: str = ""
    index = None
    isList = False
    for part in attribute.split("."):
        match part:
            case "id":
                where_part += f"{last_root}.id"
            case "idShort":
                where_part += f"{last_root}.idShort"
            case "assetInformation":
                if "assetInformation" not in mapping:
                    mapping["assetInformation"] = 0
                match_part += f"-[:assetInformation]->(assetInformation{mapping['assetInformation']}:AssetInformation)"
                last_root = f"assetInformation{mapping['assetInformation']}"
                mapping["assetInformation"] += 1
            case "assetKind":
                where_part += f"{last_root}.assetKind"
            case "assetType":
                where_part += f"{last_root}.assetType"
            case "globalAssetId":
                where_part += f"{last_root}.globalAssetId"
            case "name":
                where_part += f"{last_root}.name"
            case "value":
                # If value is part of MultiLanguageProperty, we need to access value_type. We make sure last_root is multiLanguageProperty
                if last_root == "multiLanguageProperty":
                    where_part += f"{last_root}.value_text"
                    isList = True
                # If value is part of Reference, then value is part of keys.
                elif last_root == "reference":
                    if index is not None:
                        where_part += f"{last_root}.keys_value[{index}]"
                        index = None
                    else:
                        where_part += f"{last_root}.keys_value"
                        isList = True
                else:
                    where_part += f"{last_root}.value"
            case "externalSubjectId":
                if "externalSubjectId" not in mapping:
                    mapping["externalSubjectId"] = 0
                match_part += f"-[:externalSubjectId]->(externalSubjectId{mapping['externalSubjectId']})"
                last_root = f"externalSubjectId{mapping['externalSubjectId']}"
                mapping["externalSubjectId"] += 1
            case "type":
                # If type is part of Reference, then type is part of keys.
                if last_root == "reference":
                    if index is not None:
                        where_part += f"{last_root}.keys_type[{index}]"
                        index = None
                    else:
                        where_part += f"{last_root}.keys_type"
                        isList = True
                else:
                    where_part += f"{last_root}.type"
            case "submodels":
                if "submodels" not in mapping:
                    mapping["submodels"] = 0
                match_part += f"-[:submodels]->(submodels{mapping['submodels']}:Reference)"
                last_root = f"submodels{mapping['submodels']}"
                mapping["submodels"] += 1
            case "semanticId":
                if "semanticId" not in mapping:
                    mapping["semanticId"] = 0
                match_part += f"-[:semanticId]->(semanticId{mapping['semanticId']})"
                last_root = f"semanticId{mapping['semanticId']}"
                mapping["semanticId"] += 1
                # if semanticId is used as attribute, we need to access keys_value[0]
                if attribute.endswith("semanticId"):
                    where_part += f"{last_root}.keys_value[0]"
            case "valueType":
                where_part += f"{last_root}.valueType"
            case "language":
                # FIXME: this should be flexible. We should check here the config and build a Cypher based on config
                where_part += f"{last_root}.value_language"
                isList = True
            case _ if part.startswith("keys"):
                last_root = "reference"
                if part.index("[") + 1 < len(part) - 1:
                    index = int(part[part.index("[") + 1: part.index("]")])
            case _ if part.startswith("specificAssetIds"):
                # specificAssetIds can be referenced by index
                if "specificAssetIds" not in mapping:
                    mapping["specificAssetIds"] = 0
                if "[]" in part:
                    match_part += f"-[:specificAssetIds]->(specificAssetIds{mapping['specificAssetIds']})"
                else:
                    match_part += f"-[:specificAssetIds {{list_index: {part[-2:-1]}}}]->(specificAssetIds{mapping['specificAssetIds']})"
                last_root = f"specificAssetIds{mapping['specificAssetIds']}"
                mapping["specificAssetIds"] += 1
            case _:
                raise ValueError(f"Unknown attribute element in field: {part}")

    return where_part, match_part, isList


def _convert_field(field: Field, mapping: dict[str, int]) -> Tuple[str, str, bool]:
    """
    Convert an AST Field node to Cypher where part and match part.

    The AST Field `name` is expected in the form "<root>#<attribute_path>".
    Example: "$sm#idShort" or "$sme.myElement#value"

    Returns:
        (where_part, match_part, isList)
    """
    root, attribute = field.name.split("#")
    match_part, last_root = _convert_root(root, mapping)
    where_part, match_addition, isList = _convert_attribute_elements(attribute, last_root, mapping)
    match_part += match_addition
    return where_part, match_part, isList


def _convert_value(value: Value, mapping: dict[str, int]) -> Tuple[str, str, bool]:
    """
    Convert an AST Value node to a Cypher query string and associated fields.

    Returns:
        For Field: delegate to `_convert_field` and return (where_part, match_part, isList)
        For String/Number/Boolean literal: return (literal_value_string, "", False)
        For StrCast / NumCast: Not implemented (raises NotImplementedError)

    Literal string values are wrapped in single quotes in the generated Cypher.
    Numeric and boolean values are returned as-is.
    """
    match value:
        case Field():
            return _convert_field(value, mapping)
        case StrCast() | NumCast() | BoolCast() | DateTimeCast() | HexCast() | TimeCast():
            inner = _convert_value(value.inner, mapping)
            return f"{value.get_operator()}({inner[0]})", inner[1], False
        case Year() | Month() | DayOfMonth() | DayOfWeek():
            inner = _convert_value(value.inner, mapping)
            return f"{inner[0]}.{value.get_operator()}", inner[1], False
        case StringValue() | NumberValue() | BooleanValue():
            return value.value if isinstance(value.value, (int, float, bool)) else f"'{value.value}'", "", False
        case HexLiteral():
            return f"'{value.value}'", "", False
        case DateTimeLiteral():
            return f'datetime("{value.value}")', "", False
        case TimeLiteral():
            return f'time("{value.value}")', "", False
        case _:
            raise ValueError(f"Unsupported value type: {type(value)}")


def _convert_expression(exp: Expression, mapping: dict[str, int]) -> Tuple[str, list[str]]:
    """
    Convert an AST Expression node to a Cypher WHERE expression string and list of match fragments.

    Returns:
        (expression_string, list_of_match_parts)
    Behavior:
      - BinaryExpression: combines left/right values with the operator. If either side is a list
        and operator is "=", transforms the comparison into an `IN` expression in Cypher.
      - Not: negates the inner expression.
      - And / Or / Match: joins multiple operand expressions using the appropriate logical operator.
    """
    match exp:
        case BinaryExpression():
            left = _convert_value(exp.left, mapping)
            right = _convert_value(exp.right, mapping)
            operator = exp.get_operator()
            # If field returns a list, compare using IN operator
            if (left[2] or right[2]) and operator == "=":
                return f"{left[0]} IN {right[0]}", [left[1], right[1]]
            return f"{left[0]} {operator} {right[0]}", [left[1], right[1]]
        case Not():
            inner, fields = _convert_expression(exp.operand, mapping)
            return f"{exp.get_operator()} ({inner})", fields
        case And() | Or() | Match():
            inner = map(lambda e: _convert_expression(e, mapping), exp.operands)
            inner = list(inner)
            operator = exp.get_operator()
            return f"{f' {operator} '.join(i[0] for i in inner)}", [f for i in inner for f in i[1]]
        case _:
            raise ValueError(f"Unsupported expression type: {type(exp)}")


def _remove_duplicate_matches(matches: list[str]) -> list[str]:
    """
    Remove duplicate and empty Cypher match fragments from the provided list.

    Returns:
        A list of unique match fragments, preserving the original order.
    """
    seen = set()
    unique_matches = []
    for match in matches:
        if match not in seen and match != "":
            seen.add(match)
            unique_matches.append(match)
    return unique_matches


def converter(ast: Condition) -> str:
    """
    Convert an AST Condition node to a full Cypher query string.

    The returned string contains MATCH, WHERE and RETURN clauses.
    - MATCH clause is assembled from match fragments collected during expression conversion.
    - WHERE clause contains the boolean expression produced by `_convert_expression`.
    - RETURN clause returns the main identifier from the first match fragment.

    Example output:
        MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'x'})
        WHERE sme0.value = 'some'
        RETURN sme0

    Raises:
        ValueError: if the provided AST is not a Condition.
    """
    if not isinstance(ast, Condition):
        raise ValueError(f"Expected Condition node, got {type(ast)}")

    mapping: dict[str, int] = {}
    where_parts, match_parts = _convert_expression(ast.expr, mapping)

    combined_where, combined_matches = [where_parts], _remove_duplicate_matches(match_parts)

    first_match = combined_matches[0]
    match_var = re.findall(r"\((\w+):", first_match)
    return_var = match_var[0] if match_var else "sm"

    cypher = "MATCH " + "\nMATCH ".join(combined_matches)
    cypher += "\nWHERE " + " AND ".join(combined_where)
    cypher += f"\nRETURN {return_var}"
    return cypher
