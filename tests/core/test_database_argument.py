"""The client's ``database`` argument selects the database of every session it opens."""
import neo4j
import pytest

from neo4aas.core.base import BaseNeo4JClient
from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG


class _RecordingDriver:
    def __init__(self):
        self.calls = []

    def session(self, **kwargs):
        self.calls.append(kwargs)
        return _NullSession()


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, *args, **kwargs):
        return []


def _client_with_recording_driver(**kwargs) -> tuple[BaseNeo4JClient, _RecordingDriver]:
    client = BaseNeo4JClient(uri="", user="", **kwargs)
    driver = _RecordingDriver()
    client.driver = driver
    return client, driver


def test_sessions_use_the_named_database():
    client, driver = _client_with_recording_driver(database="corpus-a")

    client.execute_clause("RETURN 1")

    assert driver.calls == [{"database": "corpus-a"}]


def test_default_database_when_none_given():
    client, driver = _client_with_recording_driver()

    client.execute_clause("RETURN 1")

    assert driver.calls == [{"database": None}]


def test_session_passes_further_options_through():
    client, driver = _client_with_recording_driver(database="corpus-a")

    client.session(default_access_mode="READ")

    assert driver.calls == [{"database": "corpus-a", "default_access_mode": "READ"}]


@pytest.mark.integration
def test_unknown_database_is_not_silently_replaced_by_default(neo4j_params, neo4j_available_or_skip):
    client = AASNeo4JClient(
        uri=neo4j_params["uri"],
        user=neo4j_params["user"],
        password=neo4j_params["password"],
        model_config=AAS_NEO4J_MODEL_CONFIG,
        auto_optimize=False,
        database="does-not-exist",
    )
    try:
        with pytest.raises(neo4j.exceptions.ClientError):
            client.execute_clause("RETURN 1", single=True)
    finally:
        client.driver.close()
