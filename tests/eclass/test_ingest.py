"""Tests for ECLASS ingest: CD building/dedup (unit) + Neo4j push (integration)."""

import pytest

from neo4aas.eclass.ingest import (
    build_concept_descriptions,
    push_concept_descriptions,
)
from neo4aas.eclass.models import (
    EclassClass,
    EclassProperty,
    EclassUnit,
    LangText,
    ParseResult,
)


def _result():
    return ParseResult(
        classes=[
            EclassClass(irdi="0173-1#01-AAA001#001", preferred_name=[LangText("de", "A")]),
        ],
        properties=[
            EclassProperty(irdi="0173-1#02-BBB001#001", preferred_name=[LangText("de", "P")]),
            # duplicate IRDI (same property reused by another class/segment)
            EclassProperty(irdi="0173-1#02-BBB001#001", preferred_name=[LangText("de", "P again")]),
        ],
        units=[
            EclassUnit(irdi="0173-1#05-CCC001#001", short_name="mm", preferred_name="Millimeter"),
        ],
    )


def test_build_dedups_by_id():
    cds = build_concept_descriptions(_result())

    ids = [cd.id for cd in cds]
    assert ids.count("0173-1#02-BBB001#001") == 1  # duplicate collapsed
    assert set(ids) == {
        "0173-1#01-AAA001#001",
        "0173-1#02-BBB001#001",
        "0173-1#05-CCC001#001",
    }


# ---------------------------------------------------------------------------
# Integration (live Neo4j via the disposable container fixture)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_push_concept_descriptions(aas_client):
    cds = build_concept_descriptions(_result())

    added = push_concept_descriptions(aas_client, cds)
    assert added == 3
    assert aas_client.count_identifiables() == 3

    # idempotent: a second push adds nothing
    assert push_concept_descriptions(aas_client, cds) == 0
    assert aas_client.count_identifiables() == 3

    # overwrite=True replaces existing CDs (count unchanged, all rewritten)
    assert push_concept_descriptions(aas_client, cds, overwrite=True) == 3
    assert aas_client.count_identifiables() == 3

    # a pushed CD round-trips back out with its native display name (no DataSpec)
    out = aas_client.get_identifiable("0173-1#05-CCC001#001")
    assert out["id"] == "0173-1#05-CCC001#001"
    assert "embeddedDataSpecifications" not in out
    assert out["displayName"][0]["text"] == "Millimeter"
    assert out["idShort"] == "mm"
