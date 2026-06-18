import os
import pytest
import neo4j
from neo4j.exceptions import ServiceUnavailable, AuthError

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG


@pytest.fixture(scope="session")
def neo4j_params():
    """Neo4j connection parameters for integration tests.

    By default a fresh, disposable Neo4j container is started for the test
    session (via testcontainers) and torn down afterwards — so integration
    tests never touch a real database, and the destructive ``aas_client``
    fixture can only ever wipe the throwaway container.

    To run against an already-running Neo4j instead, set ``NEO4J_URI`` (and
    optionally ``NEO4J_USER`` / ``NEO4J_PASSWORD``). WARNING: the ``aas_client``
    fixture wipes that database — never point it at data you want to keep.
    """
    # Explicit override: use the provided instance, start no container.
    if os.environ.get("NEO4J_URI"):
        yield {
            "uri": os.environ["NEO4J_URI"],
            "user": os.environ.get("NEO4J_USER", "neo4j"),
            "password": os.environ.get("NEO4J_PASSWORD", "12345678"),
        }
        return

    # Default: spin up a disposable container for the whole session.
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip(
            "testcontainers not installed and NEO4J_URI not set; "
            "run `pip install \".[test]\"` or set NEO4J_URI to a throwaway DB."
        )

    container = (
        Neo4jContainer("neo4j:5", password="12345678")
        .with_env("NEO4J_PLUGINS", '["apoc"]')
        .with_env("NEO4J_server_memory_heap_max__size", "1G")
        .with_env("NEO4J_server_memory_pagecache_size", "512m")
    )
    try:
        container.start()
    except Exception as exc:  # Docker unavailable, image pull failure, ...
        pytest.skip(f"Could not start disposable Neo4j container: {exc}")

    try:
        yield {
            "uri": container.get_connection_url(),
            "user": container.username,
            "password": container.password,
        }
    finally:
        container.stop()


@pytest.fixture(scope="session")
def neo4j_available_or_skip(neo4j_params):
    """Verify Neo4j is reachable; skip all integration tests if not."""
    driver = neo4j.GraphDatabase.driver(
        neo4j_params["uri"],
        auth=(neo4j_params["user"], neo4j_params["password"]),
    )
    try:
        driver.verify_connectivity()
    except (ServiceUnavailable, AuthError) as exc:
        pytest.skip(f"Neo4j not available: {exc}")
    finally:
        driver.close()


@pytest.fixture
def aas_client(neo4j_params, neo4j_available_or_skip):
    """
    Fresh AASNeo4JClient with a clean database for each test.

    A new instance resets in-memory deduplication state (uid_counter,
    deduplicated_nodes). The DB is wiped before and after each test.
    """
    client = AASNeo4JClient(
        uri=neo4j_params["uri"],
        user=neo4j_params["user"],
        password=neo4j_params["password"],
        model_config=AAS_NEO4J_MODEL_CONFIG,
    )
    client._remove_all()
    yield client
    client._remove_all()
    client.driver.close()
