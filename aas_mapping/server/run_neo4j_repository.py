"""
WSGI entry point for AAS Repository Server with Neo4j backend and AASQL query support.
Uses Neo4jWSGIApp which extends basyx's WSGIApp with /query/shells and /query/submodels routes.
"""
import os

from app.services.run_repository import setup_logger, build_storage
from aas_mapping.server.neo4j_wsgi_app import Neo4jWSGIApp

logger = setup_logger()

env_input = os.getenv("INPUT", "/input")
env_storage = os.getenv("STORAGE", "/storage")
env_storage_persistency = os.getenv("STORAGE_PERSISTENCY", "false").lower() in {"1", "true", "yes"}
env_storage_overwrite = os.getenv("STORAGE_OVERWRITE", "false").lower() in {"1", "true", "yes"}
env_api_base_path = os.getenv("API_BASE_PATH")

wsgi_optparams = {"base_path": env_api_base_path} if env_api_base_path else {}

storage_files, supp_files = build_storage(
    env_input, env_storage, env_storage_persistency, env_storage_overwrite, logger
)

application = Neo4jWSGIApp(storage_files, supp_files, **wsgi_optparams)

if __name__ == "__main__":
    logger.info("WSGI entrypoint created. Serve with uWSGI/Gunicorn/etc.")
