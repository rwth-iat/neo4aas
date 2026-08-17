import json
import re
from pathlib import Path

import pytest

from neo4aas.core.query.aasql_to_ast import (
    parse_aasql_query,
    parse_aasql_full,
)
from neo4aas.core.query.ast_to_cypher import (
    converter,
    converter_full,
)
from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher


from tests.paths import AST_DIR as _AST_DIR, QUERIES_DIR as _QUERY_DIR

_FIXTURE_STEMS = sorted(p.stem for p in _QUERY_DIR.glob("*.json"))


def _normalize_cypher(s: str) -> str:
    """Collapse internal whitespace and drop blank lines for Cypher comparison.

    The compiler is deterministic, so equality holds modulo formatting noise
    between hand-authored and generated fixtures.
    """
    lines = []
    for raw in s.splitlines():
        stripped = re.sub(r"\s+", " ", raw).strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


@pytest.mark.parametrize("stem", _FIXTURE_STEMS)
def test_schema_valid(stem, aasql_v32_validator):
    json_path = _QUERY_DIR / f"{stem}.json"
    with open(json_path) as f:
        data = json.load(f)
    errors = sorted(aasql_v32_validator.iter_errors(data), key=lambda e: e.path)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.mark.parametrize("stem", _FIXTURE_STEMS)
def test_parse_to_ast(stem):
    json_path = _QUERY_DIR / f"{stem}.json"
    repr_path = _AST_DIR / f"{stem}.repr"
    if not repr_path.is_file():
        pytest.skip(f"no .repr fixture for {stem}")
    with open(json_path) as f:
        data = json.load(f)
    expected = repr_path.read_text()
    if "$select" in data:
        actual = repr(parse_aasql_full(data))
    else:
        actual = repr(parse_aasql_query(data))
    assert actual == expected


def test_cross_root_join_scopes_sm_under_aas():
    """Mixing $aas with $sme bridges via :references and returns the AAS."""
    data = {
        "$condition": {
            "$and": [
                {"$eq": [{"$field": "$aas#idShort"}, {"$strVal": "MyShell"}]},
                {"$eq": [{"$field": "$sme.Color#value"}, {"$strVal": "red"}]},
            ]
        }
    }
    cypher = converter(parse_aasql_query(data))
    assert "(aas)-[:submodels]->(:Reference)-[:references]->(sm)" in cypher
    assert cypher.rstrip().endswith("RETURN DISTINCT aas")


def test_target_param_forces_return_var():
    """An explicit target overrides the default precedence (endpoint-typed result)."""
    data = {"$condition": {"$eq": [{"$field": "$sm#idShort"}, {"$strVal": "TechnicalData"}]}}
    assert converter(parse_aasql_query(data)).rstrip().endswith("RETURN DISTINCT sm")
    assert converter(parse_aasql_query(data), target="aas").rstrip().endswith("RETURN DISTINCT aas")


@pytest.mark.parametrize("stem", _FIXTURE_STEMS)
def test_compile_to_cypher(stem):
    json_path = _QUERY_DIR / f"{stem}.json"
    cypher_path = _QUERY_DIR / f"{stem}.cypher"
    if not cypher_path.is_file():
        pytest.skip(f"no .cypher fixture for {stem}")
    with open(json_path) as f:
        data = json.load(f)
    expected = _normalize_cypher(cypher_path.read_text())
    if "$select" in data:
        actual = _normalize_cypher(converter_full(parse_aasql_full(data)))
    else:
        actual = _normalize_cypher(converter(parse_aasql_query(data)))
    assert actual == expected


def _quote_is_escaped(cypher: str, raw: str) -> bool:
    """True if `raw` (containing a single quote) appears in a Cypher-safe escaped form.

    Valid single-quote escapes inside a single-quoted Cypher string literal are ``\\'``
    or ``''``. The raw, unescaped form (e.g. ``'O'Brien'``) is an injection / syntax bug.
    """
    return raw.replace("'", "\\'") in cypher or raw.replace("'", "''") in cypher


def test_string_literal_with_quote_is_escaped():
    """A $strVal containing a single quote must be emitted escaped, not raw (no injection)."""
    query = {"$condition": {"$eq": [{"$field": "$sm#idShort"}, {"$strVal": "O'Brien"}]}}
    cypher = convert_aasql_to_cypher(query)
    assert _quote_is_escaped(cypher, "O'Brien"), f"unescaped quote injected: {cypher!r}"


def test_idshort_path_with_quote_is_escaped():
    """An idShort path segment containing a single quote must be emitted escaped."""
    query = {"$condition": {"$eq": [{"$field": "$sme.Va'lue#idShort"}, {"$strVal": "x"}]}}
    cypher = convert_aasql_to_cypher(query)
    assert _quote_is_escaped(cypher, "Va'lue"), f"unescaped quote injected into idShort: {cypher!r}"
