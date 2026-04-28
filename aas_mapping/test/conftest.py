import json
import os
from pathlib import Path

import pytest
import neo4j
from neo4j.exceptions import ServiceUnavailable, AuthError

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG


@pytest.fixture(scope="session")
def aasql_v32_validator():
    """Compiled jsonschema validator for the vendored AASQL v3.2 query schema."""
    import jsonschema

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "aas_neo4j_adapter"
        / "querification"
        / "spec"
        / "query-json-schema-v3.2.json"
    )
    with open(schema_path) as f:
        schema = json.load(f)
    return jsonschema.Draft7Validator(schema)


@pytest.fixture(scope="session")
def neo4j_params():
    return {
        "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "12345678"),
    }


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
