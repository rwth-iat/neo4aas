"""Configuration for the aas4graph MCP server, sourced from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Neo4jConnectionConfig:
    """Neo4j connection parameters for the MCP server."""

    uri: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "Neo4jConnectionConfig":
        """Build connection config from NEO4J_* environment variables.

        Falls back to the local-development defaults documented in the README.
        """
        return cls(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "12345678"),
        )
