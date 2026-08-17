"""Integration test for ECLASS class->property HAS_PROPERTY edge derivation."""

import pytest

from aas_mapping.eclass.derive_edges import derive_has_property_edges
from aas_mapping.eclass.ingest import build_concept_descriptions, push_concept_descriptions
from aas_mapping.eclass.models import EclassClass, EclassProperty, LangText, ParseResult

pytestmark = pytest.mark.integration


def _has_property_count(client, cls_id: str) -> int:
    clause = (
        "MATCH (:ConceptDescription {id: $id})-[:HAS_PROPERTY]->(p:ConceptDescription) "
        "RETURN count(p) AS c"
    )
    with client.driver.session() as session:
        return session.run(clause, id=cls_id).single()["c"]


def test_derive_has_property_edges(aas_client):
    cls = EclassClass(
        irdi="0173-1#01-AAA001#001",
        preferred_name=[LangText("de", "Klasse")],
        property_refs=["0173-1#02-BBB001#001", "0173-1#02-BBB002#001", "0173-1#02-MISSING#001"],
    )
    result = ParseResult(
        classes=[cls],
        properties=[
            EclassProperty(irdi="0173-1#02-BBB001#001", preferred_name=[LangText("de", "P1")]),
            EclassProperty(irdi="0173-1#02-BBB002#001", preferred_name=[LangText("de", "P2")]),
        ],
    )
    push_concept_descriptions(aas_client, build_concept_descriptions(result))

    created = derive_has_property_edges(aas_client, result.classes)

    # Only the two properties whose CDs exist get edges; MISSING is skipped.
    assert created == 2
    assert _has_property_count(aas_client, "0173-1#01-AAA001#001") == 2

    # Idempotent: re-deriving keeps the same two edges.
    derive_has_property_edges(aas_client, result.classes)
    assert _has_property_count(aas_client, "0173-1#01-AAA001#001") == 2
