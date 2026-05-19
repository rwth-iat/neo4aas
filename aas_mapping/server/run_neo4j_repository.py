"""
WSGI entry point for AAS Repository Server with Neo4j backend and AASQL query support.
"""
import logging
import os

from app.interfaces.repository import WSGIApp
from aas_mapping.server.backend.neo4j import build_neo4j_storage


def setup_logger() -> logging.Logger:
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)s [Server Start-up] %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


logger = setup_logger()

env_input = os.getenv("INPUT", "/input")
env_api_base_path = os.getenv("API_BASE_PATH")

wsgi_optparams = {"base_path": env_api_base_path} if env_api_base_path else {}

logger.info(
    'Loaded settings API_BASE_PATH="%s", STORAGE_BACKEND=neo4j, INPUT="%s"',
    env_api_base_path or "",
    env_input,
)

storage_files, supp_files = build_neo4j_storage(env_input, logger)

_wsgi_app = WSGIApp(storage_files, supp_files, **wsgi_optparams)

_CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"),
    ("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept, Origin"),
    ("Access-Control-Max-Age", "86400"),
]


def application(environ, start_response):
    if environ.get("REQUEST_METHOD") == "OPTIONS":
        start_response("204 No Content", _CORS_HEADERS + [("Content-Length", "0")])
        return [b""]

    def _start(status, headers, exc_info=None):
        return start_response(status, headers + _CORS_HEADERS, exc_info)

    return _wsgi_app(environ, _start)

if __name__ == "__main__":
    logger.info("WSGI entrypoint created. Serve with uWSGI/Gunicorn/etc.")
