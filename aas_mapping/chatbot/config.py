"""Chatbot configuration and shared clients (KIConnect LLM + optional Neo4j)."""

import logging
import os
import sys

import openai

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chatbot")

REPOSITORY_URL = os.getenv("REPOSITORY_URL", "http://localhost:8081/api/v3.1")

KICONNECT_BASE_URL = os.getenv("KICONNECT_BASE_URL", "https://chat.kiconnect.nrw/api/v1")
KICONNECT_API_KEY = os.getenv("KICONNECT_API_KEY", "").strip()

MODEL_LARGE = os.getenv("MODEL_LARGE", "mistralai-mistral-small-4-119b")
MODEL_SMALL = os.getenv("MODEL_SMALL", "mistralai-mistral-small-4-119b")

# Neo4j is optional: when NEO4J_URI is set, the backend is our neo4aas store and the
# cypher_read tool becomes available (read-only).
NEO4J_URI = os.getenv("NEO4J_URI", "").strip()
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()

if not KICONNECT_API_KEY:
    log.error("KICONNECT_API_KEY is not set. Set it in .env or environment.")
    sys.exit(1)

llm_client = openai.OpenAI(
    base_url=KICONNECT_BASE_URL,
    api_key=KICONNECT_API_KEY,
    max_retries=3,
    timeout=60.0,
)

_aas_client = None


def neo4j_enabled() -> bool:
    return bool(NEO4J_URI)


def get_aas_client():
    """Lazily build a read-only AASNeo4JClient (only when NEO4J_URI is configured).

    auto_optimize=False so this read-only consumer never writes schema on connect.
    The shared `aas_mapping.aas_neo4j_adapter.agent_tools` functions operate on it.
    """
    global _aas_client
    if not neo4j_enabled():
        return None
    if _aas_client is None:
        from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import (
            AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG,
        )
        _aas_client = AASNeo4JClient(
            uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD,
            model_config=AAS_NEO4J_MODEL_CONFIG, auto_optimize=False,
        )
    return _aas_client
