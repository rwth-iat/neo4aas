from __future__ import annotations
from dataclasses import dataclass
from typing import List
from abc import ABC, abstractmethod


class Node(ABC):
    """
    Abstract base class for all AST nodes.
    """
    pass


class Value(Node, ABC):
    """
    Abstract base class for all value nodes.
    """
    pass


class Expression(Node, ABC):
    """
    Abstract base class for all expression nodes.
    """
    pass


@dataclass
class Field(Value):
    """
    Represents a field in the AST.

    Attributes:
        name (str): The name of the field.
    """
    name: str

    def __repr__(self): return f'Field("{self.name}")'


@dataclass
class StringValue(Value):
    """
    Represents a string value in the AST.

    Attributes:
        value (str): The string value.
    """
    value: str

    def __repr__(self): return f'StringValue("{self.value}")'


@dataclass
class NumberValue(Value):
    """
    Represents a numeric value in the AST.

    Attributes:
        value (float): The numeric value.
    """
    value: float

    def __repr__(self): return f'NumberValue({self.value})'


@dataclass
class BooleanValue(Value):
    """
    Represents a boolean value in the AST.

    Attributes:
        value (bool): The boolean value.
    """
    value: bool

    def __repr__(self): return f'BooleanValue({self.value})'


@dataclass
class HexLiteral(Value):
    """
    Represents an xs:hexBinary literal in the AST. Format: "16#" + hex digits.
    """
    value: str

    def __repr__(self): return f'HexLiteral("{self.value}")'


@dataclass
class DateTimeLiteral(Value):
    """
    Represents an xs:dateTime literal in the AST.
    """
    value: str

    def __repr__(self): return f'DateTimeLiteral("{self.value}")'


@dataclass
class TimeLiteral(Value):
    """
    Represents an xs:time literal in the AST.
    """
    value: str

    def __repr__(self): return f'TimeLiteral("{self.value}")'


@dataclass
class StrCast(Value):
    """
    Represents a string cast operation in the AST.

    Attributes:
        inner (Value): The value to be cast to string.
    """
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "toString"

    def __repr__(self): return f"Str({self.inner})"


@dataclass
class NumCast(Value):
    """
    Represents a numeric cast operation in the AST.

    Attributes:
        inner (Value): The value to be cast to number.
    """
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "toInteger"

    def __repr__(self): return f"Num({self.inner})"


@dataclass
class HexCast(Value):
    """
    Represents a hexadecimal cast operation in the AST.

    Neo4j has no native hex type, so this maps to toString — the comparison
    is then a string equality between the cast field and a HexLiteral
    (e.g. ``'16#FF00'``).
    """
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "toString"

    def __repr__(self): return f"Hex({self.inner})"


@dataclass
class BoolCast(Value):
    """
    Represents a boolean cast operation in the AST.

    Attributes:
        inner (Value): The value to be cast to boolean.
    """
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "toBoolean"

    def __repr__(self): return f"Bool({self.inner})"


@dataclass
class DateTimeCast(Value):
    """
    Represents a datetime cast operation in the AST.
    """
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "datetime"

    def __repr__(self): return f"DateTime({self.inner})"


@dataclass
class TimeCast(Value):
    """
    Represents a time cast operation in the AST.

    Attributes:
        inner (Value): The value to be cast to time.
    """
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "time"

    def __repr__(self): return f"Time({self.inner})"


@dataclass
class Year(Value):
    """Extract the year from a datetime value (Cypher's `.year` accessor)."""
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "year"

    def __repr__(self): return f"Year({self.inner})"


@dataclass
class Month(Value):
    """Extract the month (1-12) from a datetime value."""
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "month"

    def __repr__(self): return f"Month({self.inner})"


@dataclass
class DayOfMonth(Value):
    """Extract the day-of-month (1-31) from a datetime value (Cypher's `.day`)."""
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "day"

    def __repr__(self): return f"DayOfMonth({self.inner})"


@dataclass
class DayOfWeek(Value):
    """Extract the day-of-week from a datetime value (Cypher's `.dayOfWeek`)."""
    inner: Value

    @staticmethod
    def get_operator() -> str:
        return "dayOfWeek"

    def __repr__(self): return f"DayOfWeek({self.inner})"


@dataclass
class BinaryExpression(Expression):
    """
    Represents a binary expression in the AST.

    Attributes:
        left (Value): The left operand.
        right (Value): The right operand.
    """
    left: Value
    right: Value

    @staticmethod
    @abstractmethod
    def get_operator() -> str:
        pass


@dataclass
class Eq(BinaryExpression):
    """
    Represents an equality expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "="

    def __repr__(self): return f"Eq({self.left}, {self.right})"


@dataclass
class Ne(BinaryExpression):
    """
    Represents a not-equal expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "<>"

    def __repr__(self): return f"Ne({self.left}, {self.right})"


@dataclass
class Gt(BinaryExpression):
    """
    Represents a greater-than expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return ">"

    def __repr__(self): return f"Gt({self.left}, {self.right})"


@dataclass
class Ge(BinaryExpression):
    """
    Represents a greater-than-or-equal expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return ">="

    def __repr__(self): return f"Ge({self.left}, {self.right})"


@dataclass
class Lt(BinaryExpression):
    """
    Represents a less-than expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "<"

    def __repr__(self): return f"Lt({self.left}, {self.right})"


@dataclass
class Le(BinaryExpression):
    """
    Represents a less-than-or-equal expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "<="

    def __repr__(self): return f"Le({self.left}, {self.right})"


@dataclass
class Contains(BinaryExpression):
    """
    Represents a contains expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "CONTAINS"

    def __repr__(self): return f"Contains({self.left}, {self.right})"


@dataclass
class StartsWith(BinaryExpression):
    """
    Represents a starts-with expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "STARTS WITH"

    def __repr__(self): return f"StartsWith({self.left}, {self.right})"


@dataclass
class EndsWith(BinaryExpression):
    """
    Represents an ends-with expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "ENDS WITH"

    def __repr__(self): return f"EndsWith({self.left}, {self.right})"


@dataclass
class Regex(BinaryExpression):
    """
    Represents a regex match expression in the AST.
    """

    @staticmethod
    def get_operator() -> str:
        return "=~"

    def __repr__(self): return f"Regex({self.left}, {self.right})"


@dataclass
class Match(Expression):
    """
    Represents a match expression in the AST.

    Attributes:
        operands (List[Expression]): The list of expressions to match.
    """
    operands: List[Expression]

    @staticmethod
    def get_operator() -> str:
        return "AND"

    def __repr__(self): return f"Match([{', '.join(map(str, self.operands))}])"


@dataclass
class And(Expression):
    """
    Represents a logical AND expression in the AST.

    Attributes:
        operands (List[Expression]): The list of expressions to AND together.
    """
    operands: List[Expression]

    @staticmethod
    def get_operator() -> str:
        return "AND"

    def __repr__(self): return f"And([{', '.join(map(str, self.operands))}])"


@dataclass
class Or(Expression):
    """
    Represents a logical OR expression in the AST.

    Attributes:
        operands (List[Expression]): The list of expressions to OR together.
    """
    operands: List[Expression]

    @staticmethod
    def get_operator() -> str:
        return "OR"

    def __repr__(self): return f"Or([{', '.join(map(str, self.operands))}])"


@dataclass
class Not(Expression):
    """
    Represents a logical NOT expression in the AST.

    Attributes:
        operand (Expression): The expression to negate.
    """
    operand: Expression

    @staticmethod
    def get_operator() -> str:
        return "NOT"

    def __repr__(self): return f"Not({self.operand})"


@dataclass
class Condition(Node):
    """
    Represents a top-level condition wrapper in the AST.

    Attributes:
        expr (Expression): The root expression of the condition tree.
    """
    expr: Expression

    def __repr__(self): return f"Condition({self.expr})"
