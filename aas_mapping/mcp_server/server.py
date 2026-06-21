"""
aas4graph MCP server (read-only, stdio transport).

Exposes the Neo4j-backed AAS graph as Model Context Protocol tools so that MCP
clients (Claude Desktop, Claude Code, ...) can read, query and validate AAS data
stored in Neo4j.

All tools are read-only: nothing in this module mutates the graph.

Run with::

    python -m aas_mapping.mcp_server

Connection is configured via the NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
environment variables (see config.py for defaults).
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import (
    AAS_NEO4J_MODEL_CONFIG,
    AASNeo4JClient,
)
from aas_mapping.aas_neo4j_adapter.validation import AASConstraintChecker
from aas_mapping.mcp_server.abstract import build_abstract_submodel
from aas_mapping.mcp_server.config import Neo4jConnectionConfig

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Shared state held for the lifetime of the server process."""

    client: AASNeo4JClient


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Open a single Neo4j-backed client at startup, close it at shutdown."""
    cfg = Neo4jConnectionConfig.from_env()
    logger.info("Connecting to Neo4j at %s as %s", cfg.uri, cfg.user)
    client = AASNeo4JClient(
        uri=cfg.uri,
        user=cfg.user,
        password=cfg.password,
        model_config=AAS_NEO4J_MODEL_CONFIG,
    )
    try:
        yield AppContext(client=client)
    finally:
        if client.driver is not None:
            client.driver.close()


mcp = FastMCP("aas4graph", lifespan=lifespan)


def _client(ctx: Context) -> AASNeo4JClient:
    return ctx.request_context.lifespan_context.client


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool()
def count_stats(ctx: Context) -> dict[str, int]:
    """Return graph counts: AssetAdministrationShells, Submodels, ConceptDescriptions.

    Cheap sanity check that the graph is reachable and populated.
    """
    return _client(ctx).count_identifiables_by_type()


@mcp.tool()
def get_identifiable(identifier: str, ctx: Context) -> dict[str, Any]:
    """Fetch a top-level Identifiable (AssetAdministrationShell, Submodel or
    ConceptDescription) by its global `id`, returned as an AAS JSON object.

    Args:
        identifier: The global `id` of the Identifiable (e.g. a URI/IRI).

    Raises:
        ValueError: if no Identifiable with that `id` exists in the graph.
    """
    try:
        return _client(ctx).get_identifiable(identifier)
    except KeyError:
        raise ValueError(
            f"No Identifiable found with id '{identifier}'. "
            "Use count_stats to check the graph is populated, or "
            "list_submodel_types to browse what is loaded."
        )


@mcp.tool()
def get_referable(
    parent_id: str,
    ctx: Context,
    id_short_path: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch a Referable as an AAS JSON object.

    Args:
        parent_id: Global `id` of the containing Identifiable.
        id_short_path: Dot/bracket path to the nested element, e.g.
            "MyCollection.MyList[0].MyProperty". Omit to fetch the Identifiable
            itself (same as get_identifiable).

    Raises:
        ValueError: if no Referable exists at the given id / idShort path.
    """
    try:
        return _client(ctx).get_referable(parent_id, id_short_path)
    except KeyError:
        target = parent_id if not id_short_path else f"{parent_id} -> {id_short_path}"
        raise ValueError(
            f"No Referable found at '{target}'. Check the parent id and the "
            "idShort path (dot/bracket syntax, e.g. 'Coll.List[0].Prop')."
        )


@mcp.tool()
def validate_constraints(
    ctx: Context,
    constraint_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Validate AAS data in Neo4j against the AAS specification constraints.

    Args:
        constraint_ids: Optional subset of constraint IDs (e.g. ["AASd-002",
            "AASd-022"]). Omit to run all implemented constraints.

    Returns a compliance flag, a human-readable summary, the list of checked
    constraints, and structured violation records.
    """
    checker = AASConstraintChecker(_client(ctx))
    report = checker.check(constraint_ids) if constraint_ids else checker.check_all()
    return {
        "compliant": report.is_compliant(),
        "summary": report.summary(),
        "checked_constraints": report.checked_constraints,
        "violations": [
            {
                "constraint_id": v.constraint_id,
                "description": v.description,
                "details": v.details,
            }
            for v in report.violations
        ],
    }


_LIST_SUBMODEL_TYPES_CYPHER = """
MATCH (sm:Submodel)
OPTIONAL MATCH (sm)-[:semanticId]->(sem:Reference)
RETURN sm.idShort AS idShort, sem.keys_value[0] AS semanticId, COUNT(sm) AS count
ORDER BY count DESC, idShort
""".strip()


@mcp.tool()
def list_submodel_types(ctx: Context) -> dict[str, Any]:
    """List distinct Submodel types in the graph with an instance count each.

    Groups by idShort and semanticId, sorted by count descending. Useful to
    discover which types exist before calling abstract_submodel.
    """
    rows = _client(ctx).execute_clause(_LIST_SUBMODEL_TYPES_CYPHER) or []
    types = [
        {
            "idShort": row["idShort"],
            "semanticId": row["semanticId"],
            "count": row["count"],
        }
        for row in rows
    ]
    return {"total_types": len(types), "types": types}


@mcp.tool()
def abstract_submodel(
    submodel_type: str,
    ctx: Context,
    output_format: Literal["json", "yaml"] = "json",
) -> dict[str, Any]:
    """Build an abstract Template submodel from all Submodels of a given type.

    Fetches every matching Submodel, strips instance-specific values, and returns
    the structural union as a Template-kind AAS JSON object.

    Args:
        submodel_type: The type to abstract. Matched against the Submodel's
            semanticId first (the real type discriminator); if nothing matches, it
            falls back to matching against ``idShort``. See list_submodel_types for
            the available semanticId / idShort values.
        output_format: ``"json"`` (default) returns the template as a nested dict
            under the ``"abstract_submodel"`` key. ``"yaml"`` serialises only the
            template to a YAML string under the ``"yaml"`` key.

    Raises:
        ValueError: if no Submodels of the given type are found.
    """
    client = _client(ctx)

    # semanticId is the real type discriminator; fall back to idShort if it matches
    # nothing, instead of guessing from the string shape.
    instances = client.get_submodels_by_type(submodel_type, by_semantic_id=True)
    if not instances:
        instances = client.get_submodels_by_type(submodel_type, by_semantic_id=False)

    if not instances:
        raise ValueError(
            f"No Submodels found for type '{submodel_type}'. "
            "Use list_submodel_types to browse available types."
        )

    abstract = build_abstract_submodel(instances)

    if output_format == "yaml":
        import yaml  # lazy import — optional dep

        return {
            "instance_count": len(instances),
            "submodel_type": submodel_type,
            "yaml": yaml.dump(abstract, allow_unicode=True, sort_keys=False),
        }

    return {
        "instance_count": len(instances),
        "submodel_type": submodel_type,
        "abstract_submodel": abstract,
    }


def main() -> None:
    """Entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
