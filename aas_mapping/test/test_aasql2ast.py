import json
import re
from pathlib import Path

import pytest

from aas_mapping.aas_neo4j_adapter.querification.aasql_to_ast import (
    parse_aasql_query,
    parse_aasql_full,
)
from aas_mapping.aas_neo4j_adapter.querification.ast_to_cypher import (
    converter,
    converter_full,
)


_QUERY_DIR = Path(__file__).resolve().parents[1] / "examples" / "queries"
_AST_DIR = Path(__file__).resolve().parents[1] / "examples" / "ast"

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
    assert cypher.rstrip().endswith("RETURN aas")


def test_target_param_forces_return_var():
    """An explicit target overrides the default precedence (endpoint-typed result)."""
    data = {"$condition": {"$eq": [{"$field": "$sm#idShort"}, {"$strVal": "TechnicalData"}]}}
    assert converter(parse_aasql_query(data)).rstrip().endswith("RETURN sm")
    assert converter(parse_aasql_query(data), target="aas").rstrip().endswith("RETURN aas")


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
