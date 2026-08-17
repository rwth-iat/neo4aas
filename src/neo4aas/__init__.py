"""neo4aas — map Asset Administration Shell data into Neo4j.

Layout::

    neo4aas.core         AAS <-> Neo4j mapping and the AASQL -> Cypher compiler.
                         Depends on the neo4j driver and nothing else.
    neo4aas.agent_tools  Read-only, LLM-facing tools over core (shared by mcp/chatbot).
    neo4aas.basyx_ext    basyx-python-sdk integration: Neo4jObjectStore + the
                         Repository server. Needs `neo4aas[basyx]`.
    neo4aas.eclass       ECLASS dictionary -> ConceptDescription ingestion.
    neo4aas.mcp          MCP server app.
    neo4aas.chatbot      LangGraph chatbot app.

Only *core* symbols are re-exported here. ``Neo4jObjectStore`` deliberately is not:
re-exporting it would make a bare ``import neo4aas`` require basyx and undo the
dependency split. Import it from :mod:`neo4aas.basyx_ext` instead.
"""

from neo4aas.core.base import BaseNeo4JClient, Neo4jModelConfig
from neo4aas.core.client import AAS_NEO4J_MODEL_CONFIG, AASNeo4JClient
from neo4aas.core.query.aasql_to_cypher import convert_aasql_to_cypher

__version__ = "0.1.0"

__all__ = [
    "AASNeo4JClient",
    "AAS_NEO4J_MODEL_CONFIG",
    "BaseNeo4JClient",
    "Neo4jModelConfig",
    "convert_aasql_to_cypher",
    "__version__",
]
