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

KICONNECT_BASE_URL = os.getenv("KICONNECT_BASE_URL", "https://chat.kiconnect.nrw/api/v1")
KICONNECT_API_KEY = os.getenv("KICONNECT_API_KEY", "").strip()

# Agent + utility model. gpt-oss-120b is the only API-callable model that does native
# tool-calling well; mistral fallback kept for reference.
MODEL_AGENT = os.getenv("MODEL_AGENT", "gpt-oss-120b")
MODEL_UTIL = os.getenv("MODEL_UTIL", "gpt-oss-120b")
MODEL_EMBED = os.getenv("MODEL_EMBED", "qwen3-embedding-8b")

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

# --- Repository registry ------------------------------------------------------------
# Two fixed backends the UI can switch between; URLs are env-overridable (the demonstrator
# compose points Lieferanten at the separate stack's published host ports).
from dataclasses import dataclass


@dataclass(frozen=True)
class RepoConfig:
    id: str
    label: str
    repository_url: str          # AAS Repository REST base (…/api/v3.1)
    neo4j_uri: str               # bolt URI ("" → no Neo4j tools for this repo)
    neo4j_user: str
    neo4j_password: str
    domain: str                  # selects the per-repo system-prompt domain hints
    aas_viewer_url: str          # AAS UI viewer base (…/aasviewer?aas=… deep links)
    public_repository_url: str   # browser-facing Repository base for viewer deep links
                                 # (repository_url may be an internal Docker name the browser can't reach)


REPOSITORIES: dict[str, RepoConfig] = {
    "pumpwerk": RepoConfig(
        id="pumpwerk",
        label="Pumping Station",
        repository_url=os.getenv("REPOSITORY_URL", "http://localhost:8081/api/v3.1"),
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687").strip(),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "12345678").strip(),
        domain="pumpwerk",
        aas_viewer_url=os.getenv("AAS_VIEWER_URL", "http://localhost:3000"),
        public_repository_url=os.getenv(
            "PUBLIC_REPOSITORY_URL", os.getenv("REPOSITORY_URL", "http://localhost:8081/api/v3.1")),
    ),
    "lieferanten": RepoConfig(
        id="lieferanten",
        label="Suppliers/Warehouse",
        repository_url=os.getenv("LIEFERANTEN_REPOSITORY_URL", "http://localhost:8084/api/v3.1"),
        neo4j_uri=os.getenv("LIEFERANTEN_NEO4J_URI", "bolt://localhost:7689").strip(),
        neo4j_user=os.getenv("LIEFERANTEN_NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("LIEFERANTEN_NEO4J_PASSWORD", "12345678").strip(),
        domain="lieferanten",
        aas_viewer_url=os.getenv("LIEFERANTEN_AAS_VIEWER_URL", "http://localhost:3000"),
        public_repository_url=os.getenv(
            "LIEFERANTEN_PUBLIC_REPOSITORY_URL",
            os.getenv("LIEFERANTEN_REPOSITORY_URL", "http://localhost:8084/api/v3.1")),
    ),
}
DEFAULT_REPO_ID = os.getenv("DEFAULT_REPO_ID", "pumpwerk")


def get_repo(repo_id: str | None) -> RepoConfig:
    """Resolve a repo id to its config; unknown/empty → the default repo."""
    return REPOSITORIES.get(repo_id or DEFAULT_REPO_ID, REPOSITORIES[DEFAULT_REPO_ID])


def neo4j_enabled(repo_id: str) -> bool:
    return bool(get_repo(repo_id).neo4j_uri)


_aas_clients: dict[str, object] = {}  # repo_id -> AASNeo4JClient (one per repo)


def get_aas_client(repo_id: str):
    """Lazily build a read-only AASNeo4JClient for the given repo (None if it has no Neo4j).

    auto_optimize=False so this read-only consumer never writes schema on connect. Cached
    per repo_id so each backend keeps its own driver/connection.
    """
    repo = get_repo(repo_id)
    if not repo.neo4j_uri:
        return None
    if repo.id not in _aas_clients:
        from neo4aas.core.client import (
            AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG,
        )
        _aas_clients[repo.id] = AASNeo4JClient(
            uri=repo.neo4j_uri, user=repo.neo4j_user, password=repo.neo4j_password,
            model_config=AAS_NEO4J_MODEL_CONFIG, auto_optimize=False,
        )
    return _aas_clients[repo.id]
