"""Derive graph edges that have no AAS ConceptDescription slot.

Class hierarchy (``isCaseOf``) and property->unit (``unit_id``) are ordinary
ExternalReferences and so already materialize as ``:references`` edges via
``AASNeo4JClient.resolve_references()``. Class->property assignment has no CD
slot, so it is modelled here as a direct custom ``:HAS_PROPERTY`` edge between
the class CD node and each of its property CD nodes (a non-spec graph
convenience layer on top of the spec-conformant CDs).
"""

from __future__ import annotations

import logging
from typing import Iterable

from neo4aas.eclass.models import EclassClass, EclassProperty
from neo4aas.core.utils import irdi_base

logger = logging.getLogger(__name__)


def _merge_in_batches(client, clause: str, rows: list[dict], batch_size: int) -> int:
    """Run a MERGE ``clause`` over ``rows`` in batches, returning the total count.

    A full ECLASS export has millions of pairs; MERGEing them in a single
    transaction exhausts server memory and drops the connection.
    """
    created = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        result = client.execute_clause(clause, single=True, params={"rows": batch})
        created += result["c"] if result else 0
    return created


def derive_has_property_edges(
    client, classes: Iterable[EclassClass], batch_size: int = 10_000
) -> int:
    """Create ``(:ConceptDescription)-[:HAS_PROPERTY]->(:ConceptDescription)`` edges.

    One edge per ``(class, property_ref)`` pair, but only when *both* CD nodes
    exist in the graph (a property whose CD wasn't ingested is skipped). MERGE
    makes it idempotent. Returns the number of edges present after the merge.

    **Version-agnostic**: classes reference a property at a specific IRDI version,
    but the dictionary may also hold older versions of that property as separate
    CDs. Matching by exact id would link only the referenced version and leave the
    others orphaned. So both endpoints are matched by their version-agnostic
    ``id_base`` (the IRDI minus ``#<version>``, stored + indexed on every CD), which
    connects a class to *every* version of each property it declares (and vice
    versa for class versions).

    The rows are committed in batches of ``batch_size``: a full ECLASS export has
    millions of class->property pairs, and MERGEing them all in a single
    transaction exhausts the server's memory and drops the connection.
    """
    # Collapse to version-agnostic bases and dedup (many version pairs share one base pair).
    seen: set[tuple[str, str]] = set()
    rows = []
    for cls in classes:
        cb = irdi_base(cls.irdi)
        for prop_irdi in cls.property_refs:
            key = (cb, irdi_base(prop_irdi))
            if key not in seen:
                seen.add(key)
                rows.append({"cls": key[0], "prop": key[1]})
    if not rows:
        return 0
    # Match on :ConceptDescription(id_base): version-agnostic, and that label+property is
    # indexed (optimize_database creates an index on ConceptDescription.id_base), so the
    # lookup stays O(rows) rather than scanning.
    clause = (
        "UNWIND $rows AS row "
        "MATCH (c:ConceptDescription {id_base: row.cls}) "
        "MATCH (p:ConceptDescription {id_base: row.prop}) "
        "MERGE (c)-[:HAS_PROPERTY]->(p) "
        "RETURN count(*) AS c"
    )
    created = _merge_in_batches(client, clause, rows, batch_size)
    logger.info("derived %d HAS_PROPERTY edges total (from %d base pairs)", created, len(rows))
    return created


def derive_has_unit_edges(
    client, properties: Iterable[EclassProperty], batch_size: int = 10_000
) -> int:
    """Create ``(:ConceptDescription)-[:HAS_UNIT]->(:ConceptDescription)`` edges.

    A property's unit of measure has no CD slot once the IEC61360 data spec is
    dropped, so the property->unit link is modelled as a custom edge — the sibling
    of ``:HAS_PROPERTY``. Built only for properties that carry a ``unit_irdi`` and
    only when both CDs exist (units come from the UnitsML companion). Matched by
    version-agnostic ``id_base`` and committed in batches, like HAS_PROPERTY.
    """
    seen: set[tuple[str, str]] = set()
    rows = []
    for prop in properties:
        if not prop.unit_irdi:
            continue
        key = (irdi_base(prop.irdi), irdi_base(prop.unit_irdi))
        if key not in seen:
            seen.add(key)
            rows.append({"prop": key[0], "unit": key[1]})
    if not rows:
        return 0
    clause = (
        "UNWIND $rows AS row "
        "MATCH (p:ConceptDescription {id_base: row.prop}) "
        "MATCH (u:ConceptDescription {id_base: row.unit}) "
        "MERGE (p)-[:HAS_UNIT]->(u) "
        "RETURN count(*) AS c"
    )
    created = _merge_in_batches(client, clause, rows, batch_size)
    logger.info("derived %d HAS_UNIT edges total (from %d base pairs)", created, len(rows))
    return created
