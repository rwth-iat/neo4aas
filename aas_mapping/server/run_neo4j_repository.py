"""
WSGI entry point for AAS Repository Server with Neo4j backend and AASQL query support.
Uses Neo4jWSGIApp which extends basyx's WSGIApp with /query/shells and /query/submodels routes.
"""
import logging
import os

from aas_mapping.server.backend.neo4j import build_neo4j_storage
from aas_mapping.server.neo4j_wsgi_app import Neo4jWSGIApp


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

application = Neo4jWSGIApp(storage_files, supp_files, **wsgi_optparams)

if __name__ == "__main__":
    logger.info("WSGI entrypoint created. Serve with uWSGI/Gunicorn/etc.")
