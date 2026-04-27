"""
AASQL AST → Neo4j Cypher compiler.

Architecture:
  parse_field()          — parses "$sme.X[].Y#attr" into a FieldPath dataclass
  VarGen                 — produces fresh, numbered variable names (sm0, sme1, …)
  CypherQueryBuilder     — stateful compiler that accumulates MATCH clauses and
                           produces the final WHERE expression; converter() is the
                           public entry point.

Key design decisions
--------------------
* **Shared Submodel variable**: all `$sm` and `$sme.*` conditions in a query reference
  the SAME Submodel node (lazily created on first use, stored in ``_sm_var``). This
  implements the hierarchical filtering model from IDTA-01002: every SME condition
  narrows down elements within the *same* Submodel.

* **$match semantics** (spec IDTA-01002 v3.2): all conditions inside ``$match`` must
  be satisfied by the **same** list element. Implemented via common-prefix detection:
  the longest shared SME path up to and including the first wildcard is emitted once,
  and each condition branch starts from the shared intermediate node.
  When no common list prefix exists ``$match`` degenerates to ``$and``.

* **Per-hop MATCH + path cache**: each SME traversal step is emitted as its own
  comma-separated MATCH element (not chained into one multi-hop pattern). Named steps
  (``SmeStep``) and indexed steps (``SmeIndex``) are cached by their path prefix so
  that two conditions referencing the same named node share one variable and avoid
  Neo4j's relationship-uniqueness constraint.  Wildcards (``SmeWildcard``) are NOT
  cached — each occurrence gets a fresh variable so that ``$and`` conditions with
  ``Collection[]`` independently bind to different children.

* **MultiLanguageProperty**: when the last SME segment is ``SmeWildcard`` and the
  attribute is ``language``, the wildcard is dropped and the preceding node gets the
  ``MultiLanguageProperty`` label. Its ``value_language`` list property is accessed,
  and equality comparisons become ``'value' IN node.value_language``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from aas_mapping.aas_neo4j_adapter.base import Neo4jModelConfig
from aas_mapping.aas_neo4j_adapter.querification.ast_nodes import (
    BinaryExpression, BoolCast, BooleanValue, Condition, DateTimeCast,
    Expression, Field, HexCast, Match, And, Or, Not, NumCast, NumberValue,
    StrCast, StringValue, TimeCast, Value,
)


# ---------------------------------------------------------------------------
# Field path data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SmeStep:
    """Named traversal step — corresponds to an idShort."""
    id_short: str


@dataclass(frozen=True)
class SmeWildcard:
    """Unnamed any-element traversal — the [] notation."""


@dataclass(frozen=True)
class SmeIndex:
    """Indexed element traversal — the [n] notation."""
    index: int


SmeSegment = Union[SmeStep, SmeWildcard, SmeIndex]


@dataclass
class FieldPath:
    root: str                        # "$aas" | "$sm" | "$sme" | "$cd"
    sme_segments: list[SmeSegment]   # empty for $aas / $sm / $cd
    attribute: str                   # e.g. "value", "idShort", "semanticId"


# ---------------------------------------------------------------------------
# Field path parser
# ---------------------------------------------------------------------------

def parse_field(field_name: str) -> FieldPath:
    """
    Parse an AASQL field identifier string into a structured FieldPath.

    Grammar (from IDTA-01002 BNF):
        <FieldIdentifier> ::= <root> ( "." <idShortPath> )? "#" <attribute>
        <idShortPath>     ::= <idShort> ( "[" [digits] "]" )* ( "." <idShortPath> )*

    Examples::

        "$aas#idShort"                   → FieldPath("$aas", [], "idShort")
        "$sm#semanticId"                 → FieldPath("$sm", [], "semanticId")
        "$sme.Weight#value"              → FieldPath("$sme", [Step("Weight")], "value")
        "$sme.X[].Y#value"               → FieldPath("$sme", [Step("X"), Wild, Step("Y")], "value")
        "$sme.X[0].Y#value"              → FieldPath("$sme", [Step("X"), Idx(0), Step("Y")], "value")
        "$sme.A[].B.C[]#language"        → FieldPath("$sme", [Step("A"), Wild, Step("B"), Step("C"), Wild], "language")
    """
    root_part, attribute = field_name.split("#", 1)
    dot_parts = root_part.split(".")
    root = dot_parts[0]   # "$aas", "$sm", "$sme", "$cd"

    sme_segments: list[SmeSegment] = []
    for part in dot_parts[1:]:
        if "[" not in part:
            sme_segments.append(SmeStep(part))
        else:
            name, rest = part.split("[", 1)
            if name:
                sme_segments.append(SmeStep(name))
            idx_str = rest.rstrip("]")
            sme_segments.append(SmeIndex(int(idx_str)) if idx_str else SmeWildcard())

    return FieldPath(root, sme_segments, attribute)


# ---------------------------------------------------------------------------
# Variable name generator
# ---------------------------------------------------------------------------

class VarGen:
    """Produces fresh, sequentially numbered variable names."""

    def __init__(self) -> None:
        self._c: dict[str, int] = {}

    def fresh(self, prefix: str) -> str:
        n = self._c.get(prefix, 0)
        self._c[prefix] = n + 1
        return f"{prefix}{n}"


# ---------------------------------------------------------------------------
# $match prefix helpers
# ---------------------------------------------------------------------------

def _longest_common_list_prefix(fps: list[FieldPath]) -> list[SmeSegment]:
    """
    Return the longest common prefix of SME segments across all FieldPaths,
    stopped at (and including) the first ``SmeWildcard`` or ``SmeIndex``.

    Returns ``[]`` when no shared list traversal exists → ``$match`` degenerates
    to ``$and``.
    """
    if not fps:
        return []
    segs_list = [fp.sme_segments for fp in fps]
    min_len = min(len(s) for s in segs_list)
    common: list[SmeSegment] = []
    for i in range(min_len):
        seg = segs_list[0][i]
        if not all(s[i] == seg for s in segs_list):
            break
        common.append(seg)
        if isinstance(seg, (SmeWildcard, SmeIndex)):
            break
    if common and isinstance(common[-1], (SmeWildcard, SmeIndex)):
        return common
    return []


# ---------------------------------------------------------------------------
# MultiLanguageProperty path normalization
# ---------------------------------------------------------------------------

def _normalize_mlp_segments(segments: list[SmeSegment], attribute: str) -> list[SmeSegment]:
    """
    When the last segment is a ``SmeWildcard`` and ``attribute`` is ``'language'``,
    remove it: the preceding ``SmeStep`` is the ``MultiLanguageProperty`` node whose
    ``value_language`` list is accessed directly.
    """
    if segments and isinstance(segments[-1], SmeWildcard) and attribute == "language":
        return segments[:-1]
    return segments


def _terminal_label(segments: list[SmeSegment], attribute: str) -> str:
    """Label for the terminal SME node in a path."""
    if attribute == "language":
        return "MultiLanguageProperty"
    return "SubmodelElement"


# ---------------------------------------------------------------------------
# Main compiler
# ---------------------------------------------------------------------------

class CypherQueryBuilder:
    """
    Stateful AASQL-AST → Cypher compiler.

    Usage::

        builder = CypherQueryBuilder()
        cypher  = builder.compile(condition_node)
    """

    def __init__(self, config: Optional[Neo4jModelConfig] = None) -> None:
        self._config = config
        self._vargen = VarGen()
        self._matches: list[str] = []    # ordered, deduplicated MATCH clauses
        self._sm_var: Optional[str] = None   # shared across all $sm / $sme refs
        self._root_var: str = "sm0"
        # Path cache: maps (SmeSegment, ...) prefix → terminal variable at that depth.
        # Allows multiple conditions referencing the same SME path to share one variable
        # and one MATCH element, avoiding Neo4j's relationship-uniqueness constraint
        # (which prevents the same relationship instance from appearing in two patterns
        # within a single MATCH clause).
        self._sme_path_cache: dict[tuple, str] = {}

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def compile(self, condition: Condition) -> str:
        where = self._compile_expr(condition.expr)
        match_str = ",\n      ".join(self._matches)
        return f"MATCH {match_str}\nWHERE {where}\nRETURN {self._root_var}"

    # ------------------------------------------------------------------
    # Shared Submodel variable
    # ------------------------------------------------------------------

    def _get_sm_var(self) -> str:
        """Return the shared Submodel variable, creating it on first call."""
        if self._sm_var is None:
            self._sm_var = self._vargen.fresh("sm")
            self._root_var = self._sm_var
            self._add_match(f"({self._sm_var}:Submodel)")
        return self._sm_var

    # ------------------------------------------------------------------
    # MATCH clause management
    # ------------------------------------------------------------------

    def _add_match(self, clause: str) -> None:
        if clause and clause not in self._matches:
            self._matches.append(clause)

    # ------------------------------------------------------------------
    # Config-driven property name resolution
    # ------------------------------------------------------------------

    def _flat_prop(self, label: str, prop: str, subfield: str) -> str:
        """
        Return the flattened property name for a list-of-dicts attribute.

        If ``_config`` is set, validates that ``prop`` is listed under ``label``
        in ``list_of_dicts_prop_as_multiple_list_props``.  Raises ``ValueError``
        if it is not, so that mismatches between the graph schema and the query
        are caught early rather than silently producing wrong property names.

        When no config is provided the name is derived by convention only:
        ``{prop}_{subfield}`` (e.g. ``"keys_value"``, ``"value_language"``).
        """
        if self._config is not None:
            flattened = self._config.list_of_dicts_prop_as_multiple_list_props.get(label, [])
            if prop not in flattened:
                raise ValueError(
                    f"Property '{prop}' on '{label}' is not configured as a flattened "
                    f"list-of-dicts property in Neo4jModelConfig. "
                    f"Cannot generate '{prop}_{subfield}' query attribute."
                )
        return f"{prop}_{subfield}"

    # ------------------------------------------------------------------
    # Expression compilation
    # ------------------------------------------------------------------

    def _compile_expr(self, expr: Expression) -> str:
        if isinstance(expr, BinaryExpression):
            return self._compile_binary(expr)
        if isinstance(expr, Match):
            return self._compile_match_expr(expr)
        if isinstance(expr, And):
            parts = []
            for op in expr.operands:
                inner = self._compile_expr(op)
                parts.append(f"({inner})" if isinstance(op, (And, Or)) else inner)
            return " AND ".join(parts)
        if isinstance(expr, Or):
            parts = []
            for op in expr.operands:
                inner = self._compile_expr(op)
                parts.append(f"({inner})" if isinstance(op, (And, Or)) else inner)
            return " OR ".join(parts)
        if isinstance(expr, Not):
            inner = self._compile_expr(expr.operand)
            return f"NOT ({inner})"
        raise ValueError(f"Unsupported expression type: {type(expr)}")

    def _compile_binary(self, exp: BinaryExpression) -> str:
        lhs, lhs_is_list = self._compile_value(exp.left)
        rhs, rhs_is_list = self._compile_value(exp.right)
        op = exp.get_operator()
        if (lhs_is_list or rhs_is_list) and op == "=":
            # List property: use Cypher IN operator
            if lhs_is_list:
                return f"{rhs} IN {lhs}"
            return f"{lhs} IN {rhs}"
        return f"{lhs} {op} {rhs}"

    # ------------------------------------------------------------------
    # $match compilation
    # ------------------------------------------------------------------

    def _compile_match_expr(self, match: Match) -> str:
        """
        Compile a ``$match`` expression with correlated list traversal.

        Detects the longest common SME path prefix (ending at the first
        wildcard/index), emits it once as a shared MATCH pattern, then branches
        each operand from the shared intermediate node.

        Degenerates to ``$and`` when no common list prefix is found.
        """
        binary_ops = [
            op for op in match.operands
            if isinstance(op, BinaryExpression) and isinstance(op.left, Field)
        ]
        fps = [parse_field(op.left.name) for op in binary_ops]
        common = _longest_common_list_prefix(fps)

        if not common:
            return " AND ".join(self._compile_expr(op) for op in match.operands)

        # Emit the shared prefix traversal once
        shared_var = self._build_shared_prefix(common)

        # Compile each operand branching from shared_var
        where_parts: list[str] = []
        for operand in match.operands:
            if not isinstance(operand, BinaryExpression) or not isinstance(operand.left, Field):
                where_parts.append(self._compile_expr(operand))
                continue

            fp = parse_field(operand.left.name)
            remaining = _normalize_mlp_segments(
                fp.sme_segments[len(common):], fp.attribute
            )
            term_label = _terminal_label(remaining, fp.attribute)
            term_var = self._build_sme_branch(remaining, term_label, shared_var)
            prop_expr, is_list = self._resolve_attribute(fp, term_var)

            rhs, _ = self._compile_value(operand.right)
            op = operand.get_operator()
            if is_list and op == "=":
                where_parts.append(f"{rhs} IN {prop_expr}")
            else:
                where_parts.append(f"{prop_expr} {op} {rhs}")

        return " AND ".join(where_parts)

    def _build_shared_prefix(self, common: list[SmeSegment]) -> str:
        """
        Emit per-hop MATCH elements for the common prefix segments.
        Returns the variable of the shared "list element" node (after the
        first wildcard/index).
        """
        sm_var = self._get_sm_var()
        cur_var = sm_var
        rel = "submodelElements"

        for seg in common:
            next_var = self._vargen.fresh("sme")
            if isinstance(seg, SmeStep):
                clause = f"({cur_var})-[:{rel}]->({next_var}:SubmodelElement {{idShort: '{seg.id_short}'}})"
            elif isinstance(seg, SmeWildcard):
                clause = f"({cur_var})-[:{rel}]->({next_var}:SubmodelElement)"
            elif isinstance(seg, SmeIndex):
                clause = f"({cur_var})-[:{rel} {{list_index: {seg.index}}}]->({next_var}:SubmodelElement)"
            self._add_match(clause)
            cur_var = next_var
            rel = "value"

        return cur_var

    def _build_sme_branch(
        self,
        segments: list[SmeSegment],
        terminal_label: str,
        start_var: str,
    ) -> str:
        """
        Emit per-hop MATCH elements for ``segments`` starting from ``start_var``.
        Returns the terminal node variable.
        """
        if not segments:
            return start_var

        cur_var = start_var
        rel = "value"

        for idx, seg in enumerate(segments):
            is_last = (idx == len(segments) - 1)
            label = terminal_label if is_last else "SubmodelElement"
            next_var = self._vargen.fresh("sme")
            if isinstance(seg, SmeStep):
                clause = f"({cur_var})-[:{rel}]->({next_var}:{label} {{idShort: '{seg.id_short}'}})"
            elif isinstance(seg, SmeWildcard):
                clause = f"({cur_var})-[:{rel}]->({next_var}:{label})"
            elif isinstance(seg, SmeIndex):
                clause = f"({cur_var})-[:{rel} {{list_index: {seg.index}}}]->({next_var}:{label})"
            self._add_match(clause)
            cur_var = next_var
            rel = "value"

        return cur_var

    # ------------------------------------------------------------------
    # Value compilation
    # ------------------------------------------------------------------

    def _compile_value(self, value: Value) -> tuple[str, bool]:
        """
        Return ``(cypher_expression, is_list)``.

        ``is_list=True`` signals that the property is a list and equality
        comparisons should use the Cypher ``IN`` operator.
        """
        if isinstance(value, Field):
            return self._compile_field(value)
        if isinstance(value, StringValue):
            return f"'{value.value}'", False
        if isinstance(value, NumberValue):
            v = value.value
            return (str(int(v)) if v == int(v) else str(v)), False
        if isinstance(value, BooleanValue):
            return str(value.value).lower(), False
        if isinstance(value, (StrCast, NumCast, BoolCast, DateTimeCast)):
            inner_expr, _ = self._compile_value(value.inner)
            return f"{value.get_operator()}({inner_expr})", False
        if isinstance(value, (HexCast, TimeCast)):
            raise NotImplementedError(
                f"{type(value).__name__} cannot be translated to Cypher: "
                "Neo4j has no native hex / time cast equivalent."
            )
        raise ValueError(f"Unsupported value type: {type(value)}")

    def _compile_field(self, field: Field) -> tuple[str, bool]:
        """Parse Field.name → FieldPath, emit MATCH patterns, return (prop_expr, is_list)."""
        fp = parse_field(field.name)
        node_var = self._resolve_root(fp)
        return self._resolve_attribute(fp, node_var)

    # ------------------------------------------------------------------
    # Root resolution
    # ------------------------------------------------------------------

    def _resolve_root(self, fp: FieldPath) -> str:
        """
        Emit MATCH patterns for the root + SME path.
        Returns the terminal node variable for attribute resolution.
        """
        match fp.root:
            case "$aas":
                self._root_var = "aas"
                self._add_match("(aas:AssetAdministrationShell)")
                return "aas"
            case "$sm":
                return self._get_sm_var()
            case "$cd":
                self._root_var = "cd"
                self._add_match("(cd:ConceptDescription)")
                return "cd"
            case "$sme":
                return self._resolve_sme_path(fp)
            case _:
                raise ValueError(f"Unknown root: {fp.root!r}")

    def _resolve_sme_path(self, fp: FieldPath) -> str:
        """
        Traverse an ``$sme`` path and return the terminal node variable.

        Each hop is emitted as a separate MATCH element so that independent
        conditions can share intermediate variables via ``_sme_path_cache``
        without conflicting with Neo4j's relationship-uniqueness constraint
        (which disallows the same relationship instance in two patterns within
        a single MATCH clause).

        Caching rules
        -------------
        * **Named steps** (``SmeStep``) and **indexed steps** (``SmeIndex``) are
          cached: the same ``idShort`` or list index within the same parent always
          refers to exactly one node (one relationship).  Two conditions on
          ``$sme.Material#value`` share ``sme0``; only one MATCH element is emitted.
        * **Wildcards** (``SmeWildcard``) are NOT cached: ``[]`` means "any element,"
          so each occurrence gets a fresh variable and can independently bind to a
          different child.  Two conditions ``$sme.Collection[]#value = 'A'`` and
          ``$sme.Collection[]#value = 'B'`` in ``$and`` produce two separate MATCH
          hops — ``(sme0)-[:value]->(sme1)`` and ``(sme0)-[:value]->(sme2)`` — which
          Neo4j can satisfy by different `:value` relationship instances (ChildA and
          ChildB), returning the correct result.
        """
        sm_var = self._get_sm_var()

        # Normalize: drop trailing Wildcard for MultiLanguageProperty language access
        segments = _normalize_mlp_segments(fp.sme_segments, fp.attribute)
        term_label = _terminal_label(segments, fp.attribute)

        if not segments:
            # Bare $sme: anonymous SubmodelElement from the Submodel
            sme_var = self._vargen.fresh("sme")
            self._add_match(
                f"({sm_var})-[:submodelElements]->({sme_var}:SubmodelElement)"
            )
            return sme_var

        cur_var = sm_var
        rel = "submodelElements"

        for idx, seg in enumerate(segments):
            is_last = (idx == len(segments) - 1)
            label = term_label if is_last else "SubmodelElement"

            # Only named and indexed steps are cached (they uniquely identify a node).
            # Wildcards are never cached — each occurrence binds independently.
            can_cache = isinstance(seg, (SmeStep, SmeIndex))
            path_key = tuple(segments[:idx + 1]) if can_cache else None

            if can_cache and path_key in self._sme_path_cache:
                # Reuse existing variable — the MATCH element for this hop is
                # already present (identical string → deduplicated by _add_match).
                cur_var = self._sme_path_cache[path_key]
            else:
                next_var = self._vargen.fresh("sme")
                if isinstance(seg, SmeStep):
                    clause = f"({cur_var})-[:{rel}]->({next_var}:{label} {{idShort: '{seg.id_short}'}})"
                elif isinstance(seg, SmeWildcard):
                    clause = f"({cur_var})-[:{rel}]->({next_var}:{label})"
                elif isinstance(seg, SmeIndex):
                    clause = f"({cur_var})-[:{rel} {{list_index: {seg.index}}}]->({next_var}:{label})"
                self._add_match(clause)
                if can_cache:
                    self._sme_path_cache[path_key] = next_var
                cur_var = next_var
            rel = "value"

        return cur_var

    # ------------------------------------------------------------------
    # Attribute resolution
    # ------------------------------------------------------------------

    def _resolve_attribute(self, fp: FieldPath, node_var: str) -> tuple[str, bool]:
        """
        Resolve ``fp.attribute`` relative to ``node_var``.

        May emit additional MATCH clauses for relationship traversals (e.g.,
        ``-[:semanticId]->``).

        Returns ``(cypher_property_expr, is_list)``.
        """
        attr = fp.attribute

        # semanticId shorthand → .keys[0].value
        if attr == "semanticId":
            sid_var = self._vargen.fresh("sid")
            self._add_match(f"({node_var})-[:semanticId]->({sid_var})")
            return f"{sid_var}.{self._flat_prop('Reference', 'keys', 'value')}[0]", False

        # semanticId.keys[n].value / semanticId.keys[n].type
        if attr.startswith("semanticId.keys["):
            sid_var = self._vargen.fresh("sid")
            self._add_match(f"({node_var})-[:semanticId]->({sid_var})")
            b_start = attr.index("[")
            b_end = attr.index("]")
            idx = int(attr[b_start + 1:b_end])
            sub = attr[b_end + 2:]   # skip "]."
            if sub == "value":
                return f"{sid_var}.{self._flat_prop('Reference', 'keys', 'value')}[{idx}]", False
            if sub == "type":
                return f"{sid_var}.{self._flat_prop('Reference', 'keys', 'type')}[{idx}]", False
            raise ValueError(f"Unknown semanticId sub-attribute: {sub!r}")

        # language → MultiLanguageProperty list property (is_list=True)
        if attr == "language":
            return f"{node_var}.{self._flat_prop('MultiLanguageProperty', 'value', 'language')}", True

        # Simple scalar attributes
        if attr in {"idShort", "id", "value", "valueType", "assetKind",
                    "assetType", "globalAssetId", "name"}:
            return f"{node_var}.{attr}", False

        # $aas compound attributes
        if attr.startswith("assetInformation."):
            return self._resolve_asset_info(attr[len("assetInformation."):], node_var)

        if attr.startswith("submodels["):
            b_end = attr.index("]")
            idx = int(attr[len("submodels["):b_end])
            ref_var = self._vargen.fresh("ref")
            self._add_match(
                f"({node_var})-[:submodels {{list_index: {idx}}}]->({ref_var}:Reference)"
            )
            return self._resolve_reference(attr[b_end + 2:], ref_var)

        raise ValueError(f"Unknown attribute {attr!r} for root {fp.root!r}")

    def _resolve_asset_info(self, sub_attr: str, node_var: str) -> tuple[str, bool]:
        if sub_attr in {"assetKind", "assetType", "globalAssetId"}:
            ai_var = self._vargen.fresh("ai")
            self._add_match(
                f"({node_var})-[:assetInformation]->({ai_var}:AssetInformation)"
            )
            return f"{ai_var}.{sub_attr}", False

        if sub_attr.startswith("specificAssetIds["):
            ai_var = self._vargen.fresh("ai")
            self._add_match(
                f"({node_var})-[:assetInformation]->({ai_var}:AssetInformation)"
            )
            b_end = sub_attr.index("]")
            idx_str = sub_attr[len("specificAssetIds["):b_end]
            sa_var = self._vargen.fresh("sa")
            if idx_str:
                self._add_match(
                    f"({ai_var})-[:specificAssetIds {{list_index: {idx_str}}}]->({sa_var})"
                )
            else:
                self._add_match(f"({ai_var})-[:specificAssetIds]->({sa_var})")
            leaf = sub_attr[b_end + 2:]
            if leaf in ("name", "value"):
                return f"{sa_var}.{leaf}", False
            if leaf.startswith("externalSubjectId"):
                esid_var = self._vargen.fresh("esid")
                self._add_match(f"({sa_var})-[:externalSubjectId]->({esid_var})")
                if leaf == "externalSubjectId":
                    return f"{esid_var}.{self._flat_prop('Reference', 'keys', 'value')}[0]", False
                return self._resolve_reference(leaf[len("externalSubjectId."):], esid_var)
        raise ValueError(f"Unknown assetInformation sub-attribute: {sub_attr!r}")

    def _resolve_reference(self, sub_attr: str, ref_var: str) -> tuple[str, bool]:
        if sub_attr == "type":
            return f"{ref_var}.{self._flat_prop('Reference', 'keys', 'type')}", True
        if sub_attr.startswith("keys["):
            b_end = sub_attr.index("]")
            idx_str = sub_attr[len("keys["):b_end]
            leaf = sub_attr[b_end + 2:]
            if idx_str:
                idx = int(idx_str)
                if leaf == "value":
                    return f"{ref_var}.{self._flat_prop('Reference', 'keys', 'value')}[{idx}]", False
                if leaf == "type":
                    return f"{ref_var}.{self._flat_prop('Reference', 'keys', 'type')}[{idx}]", False
            else:
                if leaf == "value":
                    return f"{ref_var}.{self._flat_prop('Reference', 'keys', 'value')}", True
                if leaf == "type":
                    return f"{ref_var}.{self._flat_prop('Reference', 'keys', 'type')}", True
        raise ValueError(f"Unknown reference sub-attribute: {sub_attr!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def converter(ast: Condition, config: Optional[Neo4jModelConfig] = None) -> str:
    """
    Convert an AST ``Condition`` node into a complete Neo4j Cypher query string.

    The returned string contains ``MATCH``, ``WHERE``, and ``RETURN`` clauses.

    Args:
        ast: Root ``Condition`` node produced by the AASQL parser.
        config: Optional ``Neo4jModelConfig``.  When provided, property names
                for flattened list-of-dicts attributes (e.g. ``keys_value``,
                ``value_language``) are validated against
                ``list_of_dicts_prop_as_multiple_list_props``.  A
                ``ValueError`` is raised if a queried attribute is not
                configured.  When ``None``, names are derived by convention
                (``{prop}_{subfield}``) without validation.

    Raises:
        ValueError: if ``ast`` is not a ``Condition`` node, or if ``config``
                    is provided and a required flattened property is missing
                    from its configuration.
    """
    if not isinstance(ast, Condition):
        raise ValueError(f"Expected Condition node, got {type(ast)}")
    return CypherQueryBuilder(config=config).compile(ast)
