"""
aas4graph MCP server (read-only, stdio transport).

Exposes the Neo4j-backed AAS graph as Model Context Protocol tools so that MCP
clients (Claude Desktop, Claude Code, ...) can read, query and validate AAS data
stored in Neo4j.

All tools are read-only: nothing in this module mutates the graph.

Run with::

    python -m neo4aas.mcp

Connection is configured via the NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
environment variables (see config.py for defaults).
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP

from neo4aas.core.client import (
    AAS_NEO4J_MODEL_CONFIG,
    AASNeo4JClient,
)
from neo4aas import agent_tools
from neo4aas.mcp.config import Neo4jConnectionConfig

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


# Tools are thin wrappers over the shared, transport-agnostic agent_tools functions,
# so the MCP server and the chatbot agent expose the same behaviour.


@mcp.tool()
def count_stats(ctx: Context) -> dict[str, int]:
    """Return graph counts: AssetAdministrationShells, Submodels, ConceptDescriptions.

    Cheap sanity check that the graph is reachable and populated.
    """
    return agent_tools.count_stats(_client(ctx))


@mcp.tool()
def get_identifiable(identifier: str, ctx: Context) -> dict[str, Any]:
    """Fetch a top-level Identifiable (AssetAdministrationShell, Submodel or
    ConceptDescription) by its global `id`, returned as an AAS JSON object.

    Args:
        identifier: The global `id` of the Identifiable (e.g. a URI/IRI).

    Raises:
        ValueError: if no Identifiable with that `id` exists in the graph.
    """
    return agent_tools.get_identifiable(_client(ctx), identifier)


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
    return agent_tools.get_referable(_client(ctx), parent_id, id_short_path)


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
    return agent_tools.validate_constraints(_client(ctx), constraint_ids)


@mcp.tool()
def list_submodel_types(ctx: Context) -> dict[str, Any]:
    """List distinct Submodel types in the graph with an instance count each.

    Groups by idShort and semanticId, sorted by count descending. Useful to
    discover which types exist before calling abstract_submodel.
    """
    return agent_tools.list_submodel_types(_client(ctx))


@mcp.tool()
def list_submodel_types_by_semantic_id(ctx: Context) -> dict[str, Any]:
    """List distinct Submodel semanticIds with an instance count each.

    Groups Submodels by semanticId only (ignoring idShort), so all instances of
    the same semantic type are collapsed into one row, sorted by count descending.
    semanticId is the real type discriminator — prefer this over list_submodel_types
    when feeding abstract_submodel. Submodels without a semanticId are grouped under
    a null semanticId.
    """
    return agent_tools.list_submodel_types_by_semantic_id(_client(ctx))


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
    return agent_tools.abstract_submodel(_client(ctx), submodel_type, output_format)


@mcp.tool()
def cypher_read(cypher: str, ctx: Context) -> dict[str, Any]:
    """Run a READ-ONLY Cypher query against the Neo4j (neo4aas) backend.

    For aggregate/graph questions the other tools don't cover: counts, distinct
    semanticIds, traversals, ECLASS/IRDI discovery. Useful labels: Identifiable,
    AssetAdministrationShell, Submodel, ConceptDescription, Referable,
    SubmodelElement, Property, MultiLanguageProperty, Reference. Relationships:
    :submodels, :submodelElements, :value, :semanticId, :references. Reference nodes
    carry keys_value[], target_id, target_id_base (IRDI without version). Always
    RETURN explicit columns; writes are rejected.

    Args:
        cypher: A read-only Cypher query.

    Raises:
        ValueError: if the query contains write operations.
    """
    return agent_tools.cypher_read(_client(ctx), cypher)


def main() -> None:
    """Entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
