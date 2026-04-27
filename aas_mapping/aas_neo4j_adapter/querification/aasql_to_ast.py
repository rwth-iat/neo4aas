from aas_mapping.aas_neo4j_adapter.querification.ast_nodes import *


def parse_aasql_value(data: dict) -> Value:
    """
    Parse AASQL value dictionary into an AST Value node.

    Args:
        data (dict): The AASQL value represented as a dictionary.
    Returns:
        Value: The AST node representing the value.
    Raises:
        ValueError: If the value type is unknown.
    """
    AASQL_TO_AST_VALUE_NODES_MAP = {
        "$field": Field,
        "$strVal": StringValue,
        "$numVal": NumberValue,
        "$boolean": BooleanValue,
    }
    AASQL_TO_AST_VALUE_NODES_WITH_CAST_MAP = {
        "$strCast": StrCast,
        "$numCast": NumCast,
        "$hexCast": HexCast,
        "$boolCast": BoolCast,
        "$dateTimeCast": DateTimeCast,
        "$timeCast": TimeCast,
    }

    for value_prop, ast_value_prop_node_type in AASQL_TO_AST_VALUE_NODES_MAP.items():
        if value_prop in data:
            return ast_value_prop_node_type(data[value_prop])
    for value_cast_prop, ast_value_prop_node_type in AASQL_TO_AST_VALUE_NODES_WITH_CAST_MAP.items():
        if value_cast_prop in data:
            return ast_value_prop_node_type(parse_aasql_value(data[value_cast_prop]))

    raise ValueError(f"Unknown value type: {data}")


def parse_aasql_expression(expr: dict) -> Expression:
    """
    Parse AASQL expression dictionary into an AST Expression node.

    Args:
        expr (dict): The AASQL expression represented as a dictionary.
    Returns:
        Expression: The AST node representing the expression.
    Raises:
        ValueError: If the expression contains unsupported operations.
    """
    if "$match" in expr:
        return Match([parse_aasql_expression(e) for e in expr["$match"]])
    if "$and" in expr:
        return And([parse_aasql_expression(e) for e in expr["$and"]])
    if "$or" in expr:
        return Or([parse_aasql_expression(e) for e in expr["$or"]])
    if "$not" in expr:
        return Not(parse_aasql_expression(expr["$not"]))

    AASQL_TO_AST_COMPARISON_OPERATORS_MAP = {
        "$eq": Eq,
        "$ne": Ne,
        "$gt": Gt,
        "$ge": Ge,
        "$lt": Lt,
        "$le": Le,
        "$contains": Contains,
        "$starts-with": StartsWith,
        "$ends-with": EndsWith,
        "$regex": Regex,
    }

    for op, cls in AASQL_TO_AST_COMPARISON_OPERATORS_MAP.items():
        if op in expr:
            a, b = expr[op]
            return cls(parse_aasql_value(a), parse_aasql_value(b))

    raise ValueError(f"Unsupported expression: {expr}")


def parse_aasql_query(query: dict) -> Condition:
    """
    Parse AASQL query dictionary into an AST Condition node.

    Args:
        query (dict): The AASQL query represented as a dictionary.
    Returns:
        Condition: The root AST node representing the query condition.
    """
    expr = parse_aasql_expression(query["$condition"])
    return Condition(expr)
