"""The AASQL compiler must never interpolate a raw list index into Cypher.

List indices in a field path (`$sme.List[0]`, `$aas#submodels[0]`,
`$aas#specificAssetIds[0]`) were previously copied verbatim into the emitted Cypher
relationship-property map, so anything between the brackets became executable Cypher —
an injection of arbitrary (write!) clauses through an AASQL string, which reaches the
compiler from the repository's /query endpoint and from LLM-generated AASQL.
"""
import json

import pytest

from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher


def _query(field: str) -> str:
    return json.dumps(
        {"$condition": {"$eq": [{"$field": field}, {"$strVal": "x"}]}}
    )


@pytest.mark.parametrize(
    "field",
    [
        "$sme.List[0}]->() DETACH DELETE x //].v#value",
        "$aas#submodels[0 OR 1=1].keys[0].value",
        "$aas#specificAssetIds[0}]->() MATCH (n) DETACH DELETE n //]",
        "$sme.List[-1].v#value",
    ],
)
def test_non_numeric_index_is_rejected(field):
    with pytest.raises(ValueError, match="index"):
        convert_aasql_to_cypher(_query(field))


def test_valid_indices_still_compile():
    cypher = convert_aasql_to_cypher(_query("$aas#submodels[0].keys[0].value"))
    assert "[:submodels {list_index: 0}]" in cypher

    cypher = convert_aasql_to_cypher(_query("$sme.List[2].v#value"))
    assert "[:value {list_index: 2}]" in cypher

    # `[]` (any list member) stays supported — it carries no index at all.
    cypher = convert_aasql_to_cypher(_query("$sme.List[].v#value"))
    assert "list_index" not in cypher


def test_attribute_path_without_a_property_is_rejected():
    """A field must end in a comparable property.

    `$aas#submodels[0]` resolves to a *node* (the Reference), not a value, and used to
    emit `WHERE  = 'x'` — Cypher that only fails at execution time in the repository.
    """
    with pytest.raises(ValueError, match="no comparable property"):
        convert_aasql_to_cypher(_query("$aas#submodels[0]"))
