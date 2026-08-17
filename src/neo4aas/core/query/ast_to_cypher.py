from typing import Optional, Tuple
import re

# Containment edge types, used for variable-length traversal when a bare `$sme` (no idShort
# path) requests a recursive search over all SubmodelElements at any depth, per the spec.
_RECURSIVE_CONTAINMENT = ":submodelElements|value|statements|annotations*1.."


def _escape(value: str) -> str:
    """Escape a string for safe embedding inside a single-quoted Cypher literal.

    The compiler emits Cypher as text (not a parameterized query), so caller-supplied
    AASQL strings — `$strVal` literals and idShort path segments — must have backslashes
    and single quotes escaped to avoid breaking or injecting into the query.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _flat(prop: str, subfield: str) -> str:
    """Flattened sub-property name, matching the import convention `{prop}_{subfield}`.

    `JsonToNeo4jImporter` writes flattened list-of-dicts properties as
    `node_properties[f"{key}_{dict_key}"]`. Centralizing the convention here keeps the
    compiler's flat accessors (e.g. `keys_value`, `value_text`) in sync with import/export.
    """
    return f"{prop}_{subfield}"


def _flattened_list_prop(mapping: dict, label: str) -> str:
    """Base name of the list-of-dicts property flattened for `label`, read from config.

    The threaded `Neo4jModelConfig` (mapping["_config"]) records which property each type
    flattens to parallel lists (MultiLanguageProperty -> "value", Reference -> "keys"); AAS
    flattens exactly one per relevant type. The compiler resolves the concrete flat names
    (value_text/value_language, keys_value/keys_type) from this base via `_flat`, so they
    follow config rather than being hard-coded. The sub-field names (text/language/value/type)
    are intrinsic to the AAS data shapes and not config-driven.
    """
    props = mapping["_config"].list_of_dicts_prop_as_multiple_list_props.get(label, [])
    if not props:
        raise ValueError(
            f"No flattened list property configured for {label}; the AASQL compiler "
            f"cannot resolve its flattened sub-fields"
        )
    return props[0]


from neo4aas.core.query.ast_nodes import *  # noqa: F401,F403


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
    local_depth = 0
    path_segments = root.split(".")[1:]

    # Bare $sme within a $match scope: correlate all refs to the same anchor node
    if not path_segments and mapping.get("_match_scopes"):
        scope = mapping["_match_scopes"][-1]
        if scope["anchor"] is not None:
            return "", f"sme{scope['anchor']}"
        scope["anchor"] = depth
        mapping["sme"] = depth + 1
        # Recursive: correlate to any SubmodelElement at any depth.
        match_part = f"(sm:Submodel)-[{_RECURSIVE_CONTAINMENT}]->(sme{depth}:SubmodelElement)"
        return match_part, f"sme{depth}"

    # Named path: deduplicate across OR arms so the same SME path reuses one MATCH variable
    if path_segments:
        path_cache = mapping.setdefault("_path_cache", {})
        if root in path_cache:
            return "", path_cache[root]

    for part in path_segments:
        if "[" in part:
            for p in part.split("["):
                if "]" not in p:
                    if local_depth == 0:
                        match_part += f"(sme{depth}:SubmodelElement {{idShort: '{_escape(p)}'}})"
                    else:
                        match_part += f"-[:value]->(sme{depth}:SubmodelElement {{idShort: '{_escape(p)}'}})"
                elif len(p) > 1:
                    match_part += f"-[:value {{list_index: {p.rstrip(']')}}}]->(sme{depth}:SubmodelElement)"
                else:
                    match_part += f"-[:value]->(sme{depth}:SubmodelElement)"
                last_root = f"sme{depth}"
                depth += 1
                local_depth += 1
        else:
            if local_depth == 0:
                match_part += f"(sme{depth}:SubmodelElement {{idShort: '{_escape(part)}'}})"
            else:
                match_part += f"-[:value]->(sme{depth}:SubmodelElement {{idShort: '{_escape(part)}'}})"
            last_root = f"sme{depth}"
            depth += 1
            local_depth += 1
    if last_root != "":
        mapping["sme"] = depth
        if path_segments:
            mapping["_path_cache"][root] = last_root
        return match_part, last_root
    # Bare `$sme` with no idShort path: recursive search over all SubmodelElements at any depth.
    match_part = f"(sm:Submodel)-[{_RECURSIVE_CONTAINMENT}]->(sme{depth}:SubmodelElement)"
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
                # If value follows a keys[..] segment, dereference the flattened keys_value list.
                if index is not None:
                    where_part += f"{last_root}.{_flat(_flattened_list_prop(mapping, 'Reference'), 'value')}[{index}]"
                    index = None
                else:
                    # SME #value is type-polymorphic and not known at compile time: a Property
                    # stores a scalar `.value`, a MultiLanguageProperty stores its text in the
                    # `value_text[]` list (flattened sub-field of MLP's configured value prop).
                    # coalesce handles both (MLP -> text list; Property -> single scalar `.value`
                    # wrapped in a list); isList makes comparisons wrap in any().
                    mlp_value = _flattened_list_prop(mapping, "MultiLanguageProperty")
                    where_part += f"coalesce({last_root}.{_flat(mlp_value, 'text')}, [{last_root}.value])"
                    isList = True
            case "externalSubjectId":
                if "externalSubjectId" not in mapping:
                    mapping["externalSubjectId"] = 0
                match_part += f"-[:externalSubjectId]->(externalSubjectId{mapping['externalSubjectId']})"
                last_root = f"externalSubjectId{mapping['externalSubjectId']}"
                mapping["externalSubjectId"] += 1
            case "type":
                # If type follows a keys[..] segment, dereference the flattened keys_type list.
                if index is not None:
                    where_part += f"{last_root}.{_flat(_flattened_list_prop(mapping, 'Reference'), 'type')}[{index}]"
                    index = None
                else:
                    where_part += f"{last_root}.type"
            case _ if part.startswith("submodels"):
                if "submodels" not in mapping:
                    mapping["submodels"] = 0
                if "[" in part:
                    idx = part[part.index("[") + 1: part.index("]")]
                    match_part += f"-[:submodels {{list_index: {idx}}}]->(submodels{mapping['submodels']}:Reference)"
                else:
                    match_part += f"-[:submodels]->(submodels{mapping['submodels']}:Reference)"
                last_root = f"submodels{mapping['submodels']}"
                mapping["submodels"] += 1
            case "semanticId":
                if "semanticId" not in mapping:
                    mapping["semanticId"] = 0
                # The semanticId node is a Reference; emit the :Reference label so the
                # planner can seek the :Reference(target_id) index (an unlabelled node
                # cannot use a label-scoped index — without it the planner expands every
                # submodel subtree before filtering, which is catastrophically slow on a
                # large repo: ~216s vs <1s for this query on the ~9k-AAS supplier graph).
                match_part += f"-[:semanticId]->(semanticId{mapping['semanticId']}:Reference)"
                last_root = f"semanticId{mapping['semanticId']}"
                mapping["semanticId"] += 1
                # When semanticId is the terminal attribute, compare against the
                # denormalized, indexed Reference.target_id (== keys_value[0], the first
                # key's value, written on every Reference at import) rather than the
                # unindexed keys_value[0] list access — see NEO4J_INTERNAL_NODE_KEYS.
                if attribute.endswith("semanticId"):
                    where_part += f"{last_root}.target_id"
            case "valueType":
                where_part += f"{last_root}.valueType"
            case "language":
                # MultiLanguageProperty.value is flattened to value_language[] (the `language`
                # sub-field of MLP's configured value prop), per the threaded Neo4jModelConfig.
                mlp_value = _flattened_list_prop(mapping, "MultiLanguageProperty")
                where_part += f"{last_root}.{_flat(mlp_value, 'language')}"
                isList = True
            case _ if part.startswith("keys"):
                if part.index("[") + 1 != len(part) - 1:
                    index = int(part[part.index("[") + 1: part.index("]")])
            case _ if part.startswith("specificAssetIds"):
                # specificAssetIds can be referenced by index
                if "specificAssetIds" not in mapping:
                    mapping["specificAssetIds"] = 0
                if "[]" in part:
                    match_part += f"-[:specificAssetIds]->(specificAssetIds{mapping['specificAssetIds']})"
                else:
                    match_part += f"-[:specificAssetIds {{list_index: {part[part.index("[") + 1: part.index("]")]}}}]->(specificAssetIds{mapping['specificAssetIds']})"
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


def _apply_cast(cast_fn, inner: Tuple[str, str, bool], mapping: dict[str, int]) -> Tuple[str, str, bool]:
    """Apply a cast/extractor (``cast_fn(operand_expr) -> cypher``) to a value.

    A scalar inner is cast directly. A **list-valued** inner (e.g. ``#value``, which always
    compiles to ``coalesce(value_text, [value])`` because the element type is unknown at
    compile time — a Property's scalar shows up as a 1-element list) must be cast *per element*
    via a list comprehension, keeping the result list-valued so ``_convert_expression`` still
    wraps the comparison in ``any(...)``. Casting the list as a whole (``toFloat(<list>)``)
    yields null and never matches — the bug this fixes for numeric/temporal thresholds on a
    Property ``#value``.
    """
    inner_expr, inner_match, inner_is_list = inner
    if inner_is_list:
        idx = mapping.get("_castvar", 0)
        mapping["_castvar"] = idx + 1
        x = f"c{idx}"
        return f"[{x} IN {inner_expr} | {cast_fn(x)}]", inner_match, True
    return cast_fn(inner_expr), inner_match, False


def _convert_value(value: Value, mapping: dict[str, int]) -> Tuple[str, str, bool]:
    """
    Convert an AST Value node to a Cypher query string and associated fields.

    Returns:
        For Field: delegate to `_convert_field` and return (where_part, match_part, isList)
        For String/Number/Boolean literal: return (literal_value_string, "", False)
        For cast wrappers: wrap inner expression with the cast operator.

    Literal string values are wrapped in single quotes in the generated Cypher.
    Numeric and boolean values are returned as-is.

    ``HexCast`` is handled separately: emits
    ``'16#' + apoc.text.format('%X', [toInteger(x)])`` using APOC Core

    Temporal cast note: ``DateTimeCast`` / ``TimeCast`` emit ``datetime(x)`` /
    ``time(x)``. This is correct because the ingestion layer stores xs:dateTime
    and xs:time property values as plain strings — Neo4j converts them at query
    time via the cast function. Do not change this to a literal comparison
    without first confirming the ingestion stores native temporal types.
    """
    match value:
        case Field():
            return _convert_field(value, mapping)
        case HexCast():
            inner = _convert_value(value.inner, mapping)
            return _apply_cast(
                lambda x: f"'16#' + apoc.text.format('%X', [toInteger({x})])", inner, mapping
            )
        case StrCast() | NumCast() | BoolCast() | DateTimeCast() | TimeCast():
            inner = _convert_value(value.inner, mapping)
            op = value.get_operator()
            return _apply_cast(lambda x: f"{op}({x})", inner, mapping)
        case Year() | Month() | DayOfMonth() | DayOfWeek():
            inner = _convert_value(value.inner, mapping)
            op = value.get_operator()
            return _apply_cast(lambda x: f"{x}.{op}", inner, mapping)
        case StringValue() | NumberValue() | BooleanValue():
            return value.value if isinstance(value.value, (int, float, bool)) else f"'{_escape(value.value)}'", "", False
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
            # Fail fast on an invalid `$regex` literal: it compiles to a Cypher `=~`
            # whose right side is a Java/PCRE regex, so a bad pattern (e.g. a bare "*",
            # which a model emits as a glob) is only rejected at execution by Neo4j
            # (Statement.SemanticError -> repository 500). Validating the literal here
            # turns it into a compile error fed back to the AASQL compose->validate->repair
            # loop, never a round-trip to the repository.
            if isinstance(exp, Regex) and isinstance(exp.right, StringValue):
                try:
                    re.compile(exp.right.value)
                except re.error as e:
                    raise ValueError(
                        f"Invalid $regex pattern {exp.right.value!r}: {e}. "
                        f"$regex takes a regular expression, not a glob — use '.*' to match any."
                    )
            left = _convert_value(exp.left, mapping)
            right = _convert_value(exp.right, mapping)
            operator = exp.get_operator()
            # If a side is multi-valued (e.g. #value on a MultiLanguageProperty -> value_text[],
            # or #language -> value_language[]), the comparison must hold for *some* element.
            # Wrap it in any(v IN list WHERE v <op> scalar), which works for every operator
            # ($eq, $contains, $starts-with, $regex, $gt, …), not just equality.
            if left[2] or right[2]:
                list_expr, scalar, scalar_on_right = (
                    (left[0], right[0], True) if left[2] else (right[0], left[0], False)
                )
                idx = mapping.get("_anyvar", 0)
                mapping["_anyvar"] = idx + 1
                v = f"v{idx}"
                predicate = f"{v} {operator} {scalar}" if scalar_on_right else f"{scalar} {operator} {v}"
                return f"any({v} IN {list_expr} WHERE {predicate})", [left[1], right[1]]
            return f"{left[0]} {operator} {right[0]}", [left[1], right[1]]
        case Not():
            inner, fields = _convert_expression(exp.operand, mapping)
            return f"{exp.get_operator()} ({inner})", fields
        case Match():
            scope: dict = {"anchor": None}
            mapping.setdefault("_match_scopes", []).append(scope)
            inner = [_convert_expression(e, mapping) for e in exp.operands]
            mapping["_match_scopes"].pop()
            operator = exp.get_operator()
            return f"{f' {operator} '.join(i[0] for i in inner)}", [f for i in inner for f in i[1]]
        case And():
            inner = list(map(lambda e: _convert_expression(e, mapping), exp.operands))
            operator = exp.get_operator()
            return f"{f' {operator} '.join(i[0] for i in inner)}", [f for i in inner for f in i[1]]
        case Or():
            inner = list(map(lambda e: _convert_expression(e, mapping), exp.operands))
            operator = exp.get_operator()
            return f"({f' {operator} '.join(i[0] for i in inner)})", [f for i in inner for f in i[1]]
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


def _select_return_var(combined_matches: list[str], target: Optional[str]) -> str:
    """Pick the RETURN variable for a query.

    When ``target`` is given it wins (an endpoint declaring its object type, e.g.
    ``"aas"`` at an AAS repository). Otherwise the outermost root present is chosen
    by precedence ``aas > sm > cd``: this is what the IDTA query-language spec means
    by "the default return type is the respective object" — combining ``$aas`` with
    ``$sm``/``$sme`` filters submodels but still yields the AAS. Falls back to the
    first MATCH fragment's variable when none of those roots are present
    (e.g. descriptor roots).
    """
    if target:
        return target
    joined = "\n".join(combined_matches)
    if "(aas:" in joined:
        return "aas"
    if "(sm:Submodel" in joined:
        return "sm"
    if "(cd:" in joined:
        return "cd"
    match_var = re.findall(r"\((\w+):", combined_matches[0])
    return match_var[0] if match_var else "sm"


def converter(ast: Condition, target: Optional[str] = None, model_config=None) -> str:
    """
    Convert an AST Condition node to a full Cypher query string.

    The returned string contains MATCH, WHERE and RETURN clauses.
    - MATCH clause is assembled from match fragments collected during expression conversion.
    - WHERE clause contains the boolean expression produced by `_convert_expression`.
    - RETURN clause returns the outermost root (see `_select_return_var`); pass
      `target` to force a specific variable for an endpoint-typed result.

    Cross-root scoping: when a query mixes `$aas` with `$sm`/`$sme`, an
    `(aas)-[:submodels]->(:Reference)-[:references]->(sm)` bridge is added so the
    submodel conditions only apply to submodels of the matching AAS, per the IDTA
    query-language spec. The bridge relies on the `:references` edges maintained by
    `AASNeo4JClient.resolve_references()`.

    Example output:
        MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'x'})
        WHERE sme0.value = 'some'
        RETURN sme0

    Raises:
        ValueError: if the provided AST is not a Condition.
    """
    if not isinstance(ast, Condition):
        raise ValueError(f"Expected Condition node, got {type(ast)}")

    if model_config is None:
        # Default to the AAS config; imported lazily to keep querification import-light
        # and avoid a module-load dependency on the AAS-specific client.
        from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG
        model_config = AAS_NEO4J_MODEL_CONFIG

    mapping: dict = {"_config": model_config}
    where_parts, match_parts = _convert_expression(ast.expr, mapping)

    combined_where, combined_matches = [where_parts], _remove_duplicate_matches(match_parts)

    joined = "\n".join(combined_matches)
    has_aas = "(aas:" in joined
    has_sm = "(sm:Submodel" in joined
    if has_aas and has_sm:
        combined_matches.append("(aas)-[:submodels]->(:Reference)-[:references]->(sm)")

    return_var = _select_return_var(combined_matches, target)

    cypher = "MATCH " + "\nMATCH ".join(combined_matches)
    cypher += "\nWHERE " + " AND ".join(combined_where)
    # DISTINCT: an AASQL find-query returns a *set* of matching objects. A traversal can
    # bind the same returned node more than once — the cross-root :references bridge
    # multiplies an AAS by each of its matching submodels, and a recursive $sme match
    # binds a submodel once per matching descendant element — so without DISTINCT the same
    # shell/submodel is emitted several times (e.g. a manufacturer present in both the
    # Nameplate and TechnicalData submodels would return its AAS twice).
    cypher += f"\nRETURN DISTINCT {return_var}"
    return cypher


def converter_full(query: Query, target: Optional[str] = None, model_config=None) -> str:
    """
    Convert a full AASQL Query (with optional $select) to Cypher.

    $select behaviour:
      - ``"id"``: RETURN clause emits ``<var>.id`` instead of the bare node.
      - absent / ``None``: returns the full anchor node — same as calling
        ``converter(query.condition)`` directly. This is the default because
        the v3.2 spec only defines ``"id"`` as a valid $select value; any
        other selection semantics are unspecified.

    `target` forces the RETURN variable (endpoint-typed result); see `converter`.

    Descriptor roots ($aasdesc / $smdesc) compile correctly but will return
    empty results until descriptor ingestion is implemented in neo4j_import.py.
    """
    cypher = converter(query.condition, target=target, model_config=model_config)
    if query.select == "id":
        prefix, var = cypher.rsplit("\nRETURN ", 1)
        cypher = prefix + "\nRETURN " + var.strip() + ".id"
    return cypher
