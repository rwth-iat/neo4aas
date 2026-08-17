"""Library entry point for ingesting ECLASS dictionaries as ConceptDescriptions.

Pipeline: parse an ECLASS package directory -> build deduplicated AAS
``ConceptDescription`` objects -> push them through ``AASNeo4JClient``. Graph
edges between the CD nodes (SUBCLASS_OF / HAS_PROPERTY / HAS_UNIT) are a separate
``derive_edges`` step; this module only materializes the CD nodes themselves.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import basyx.aas.model as model
from basyx.aas.adapter.json import AASToJsonEncoder

from neo4aas.eclass.derive_edges import derive_has_property_edges, derive_has_unit_edges
from neo4aas.eclass.models import ParseResult
from neo4aas.eclass.ontoml_parser import EclassOntomlParser
from neo4aas.eclass.spec_cd_writer import SpecCdWriter

logger = logging.getLogger(__name__)


def build_concept_descriptions(
    result: ParseResult, writer: SpecCdWriter | None = None
) -> list[model.ConceptDescription]:
    """Build CDs for every class/property/unit, deduplicated by IRDI (id).

    The same IRDI recurs across segments and between BASIC/ADVANCED, but
    ``Identifiable.id`` is unique in the graph — so the first CD seen for an id
    wins and later duplicates are dropped.
    """
    writer = writer or SpecCdWriter()
    seen: set[str] = set()
    cds: list[model.ConceptDescription] = []

    def _add(cd: model.ConceptDescription) -> None:
        if cd.id not in seen:
            seen.add(cd.id)
            cds.append(cd)

    for cls in result.classes:
        _add(writer.class_to_cd(cls))
    for prop in result.properties:
        _add(writer.property_to_cd(prop))
    for unit in result.units:
        _add(writer.unit_to_cd(unit))
    return cds


def push_concept_descriptions(
    client, cds: Iterable[model.ConceptDescription], overwrite: bool = False
) -> int:
    """Add CDs to the graph via ``client.add_identifiable``.

    :param overwrite: when False (default), CDs whose id already exists are skipped.
        When True, an existing CD with the same id is removed first and replaced by
        the ECLASS version — so the ECLASS definition wins over a CD that arrived
        earlier (e.g. an IDTA-template ConceptDescription sharing the same IRDI).
        The caller is expected to run ``resolve_references()`` afterwards to rebuild
        the ``:references`` edges (semanticId/isCaseOf) into the replaced nodes.

    Returns the number of CDs written (added or replaced).
    """
    written = 0
    for cd in cds:
        if client.identifiable_exists(cd.id):
            if not overwrite:
                continue
            client.remove_identifiable(cd.id)
        data = json.loads(json.dumps(cd, cls=AASToJsonEncoder))
        client.add_identifiable(data)
        written += 1
    logger.info("wrote %d ConceptDescriptions (overwrite=%s)", written, overwrite)
    return written


def ingest_eclass(
    package_dir: str | Path,
    sgs: Iterable[int] | None = None,
    *,
    client=None,
    neo4j_uri: str | None = None,
    neo4j_user: str | None = None,
    neo4j_password: str | None = None,
    write: bool = False,
    reset: bool = False,
) -> ParseResult:
    """Parse an ECLASS package and, when ``write``, push CDs to Neo4j.

    :param package_dir: ECLASS BASIC/ADVANCED package root directory.
    :param sgs:         Segment numbers to ingest (e.g. ``[13, 27]``); all if None.
    :param client:      Existing ``AASNeo4JClient`` to reuse; else one is built
                        from the ``neo4j_*`` parameters.
    :param write:       False = parse only (dry run). True = push to Neo4j.
    :param reset:       Wipe the database before ingest.
    """
    result = EclassOntomlParser().parse_directory(package_dir, sgs)
    logger.info(
        "parsed %d classes, %d properties, %d units",
        len(result.classes), len(result.properties), len(result.units),
    )
    if not write:
        return result

    own_client = client is None
    if own_client:
        from neo4aas.core.client import (
            AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG,
        )

        # Pass AAS_NEO4J_MODEL_CONFIG so References are modeled identically to the rest of
        # neo4aas — `keys` flattened into keys_value/keys_type on the Reference node. Without
        # it the client falls back to the empty config and `keys` becomes a separate node.
        client = AASNeo4JClient(
            uri=neo4j_uri, user=neo4j_user, password=neo4j_password,
            model_config=AAS_NEO4J_MODEL_CONFIG,
        )
    try:
        if reset:
            client._remove_all()
        # overwrite=True: the ECLASS definition wins over any CD already present with
        # the same IRDI (e.g. an IDTA-template CD imported with the operational AAS data).
        push_concept_descriptions(client, build_concept_descriptions(result), overwrite=True)
        # CD isCaseOf (superclass) and unit_id are ExternalReferences whose target IRDI is
        # another CD's id; resolve_references materializes the :references edges between them.
        client.resolve_references()
        # Class->property and property->unit have no CD slot — derive them as custom
        # :HAS_PROPERTY / :HAS_UNIT edges.
        derive_has_property_edges(client, result.classes)
        derive_has_unit_edges(client, result.properties)
    finally:
        if own_client:
            client.driver.close()
    return result
