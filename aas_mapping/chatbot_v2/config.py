"""chatbot_v2 configuration: KIConnect (OpenAI-compatible) + optional Neo4j backend.

Mirrors the env contract of the original chatbot so the two can run side-by-side.
Only the 4 API-callable KIConnect models are used (see GET /v1/models): the agent
runs on ``gpt-oss-120b`` (native tool-calling) and RAG embeds with ``qwen3-embedding-8b``.
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chatbot_v2")

REPOSITORY_URL = os.getenv("REPOSITORY_URL", "http://localhost:8081/api/v3.1")

KICONNECT_BASE_URL = os.getenv("KICONNECT_BASE_URL", "https://chat.kiconnect.nrw/api/v1")
KICONNECT_API_KEY = os.getenv("KICONNECT_API_KEY", "").strip()

# Agent + utility model. gpt-oss-120b is the only API-callable model that does native
# tool-calling well; mistral fallback kept for reference.
MODEL_AGENT = os.getenv("MODEL_AGENT", "gpt-oss-120b")
MODEL_UTIL = os.getenv("MODEL_UTIL", "gpt-oss-120b")
MODEL_EMBED = os.getenv("MODEL_EMBED", "qwen3-embedding-8b")

NEO4J_URI = os.getenv("NEO4J_URI", "").strip()
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()

# HyDE: when on, find_relevant_fields first asks the LLM for hypothetical field names
# (closer in embedding space to real idShorts than the raw question), then multi-query
# retrieves with those + the raw question. Off → plain query-embedding retrieval.
HYDE = os.getenv("HYDE", "1").strip() not in ("", "0", "false", "False")

# Optional Langfuse tracing (self-hostable, EU). Active only when keys are present.
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()

if not KICONNECT_API_KEY:
    log.error("KICONNECT_API_KEY is not set. Set it in .env or environment.")
    sys.exit(1)

_langfuse_handler = "unset"  # sentinel: not yet resolved


def get_callbacks() -> list:
    """LangChain callbacks for agent runs — a Langfuse handler when configured, else [].

    Resolved once and cached. Any import/version problem degrades to no tracing rather
    than breaking the agent.
    """
    global _langfuse_handler
    if _langfuse_handler == "unset":
        _langfuse_handler = None
        if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
            os.environ.setdefault("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY)
            os.environ.setdefault("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY)
            os.environ.setdefault("LANGFUSE_HOST", LANGFUSE_HOST)
            try:
                try:
                    from langfuse.langchain import CallbackHandler  # langfuse >=3
                except ImportError:
                    from langfuse.callback import CallbackHandler  # langfuse <3
                _langfuse_handler = CallbackHandler()
                log.info("Langfuse tracing enabled (%s)", LANGFUSE_HOST)
            except Exception as exc:  # noqa: BLE001
                log.warning("Langfuse unavailable, tracing off: %s", exc)
    return [_langfuse_handler] if _langfuse_handler else []

_aas_client = None


def neo4j_enabled() -> bool:
    return bool(NEO4J_URI)


def get_aas_client():
    """Lazily build a read-only AASNeo4JClient (only when NEO4J_URI is configured).

    auto_optimize=False so this read-only consumer never writes schema on connect.
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
