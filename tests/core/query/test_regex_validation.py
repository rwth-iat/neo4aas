"""Unit tests: the AASQL compiler rejects an invalid `$regex` literal at compile time.

A `$regex` compiles to a Cypher `=~`, whose right side is a regex. A bad pattern
(e.g. a bare ``"*"`` a model emits as a glob) is otherwise only caught at execution
by Neo4j (Statement.SemanticError -> repository 500). The compiler validates the
literal up front so it surfaces as a compile error the chatbot's
compose->validate->repair loop can feed back, never a repository round-trip.

No Neo4j needed — pure compile path.
"""
import pytest

from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher


def _regex_query(pattern: str) -> dict:
    return {
        "$condition": {
            "$regex": [{"$field": "$aas#idShort"}, {"$strVal": pattern}]
        }
    }


def test_invalid_regex_literal_rejected_at_compile():
    with pytest.raises(ValueError, match="Invalid \\$regex pattern"):
        convert_aasql_to_cypher(_regex_query("*"))


def test_valid_regex_literal_compiles():
    cypher = convert_aasql_to_cypher(_regex_query(".*Krohne.*"))
    assert "=~" in cypher
