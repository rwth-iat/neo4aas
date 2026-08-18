"""Integration tests for the semanticId query tools against the Lieferanten repo.

Regression for the SICK temperature-sensor case where the chatbot wrongly reported
"no asset with ambient temperature >= 100 C": it had searched by the English idShort
`MaxAmbientTemperature` (tops out at 70 C) and missed SICK's German `max_Umgebungstemperatur`
(125 C). Both fields share the ECLASS semanticId 0173-1#02-BAA039, so a semanticId search
unifies them.

These assert against *one specific supplier dataset*, not merely any Neo4j, so they
skip unless the Lieferanten stack is both up and loaded:

    docker compose -f deploy/lieferanten/docker-compose.yml up -d
    make test-integration
"""
import os

import pytest

os.environ.setdefault("KICONNECT_API_KEY", "dummy")  # config import requires it; LLM unused here

from neo4aas.chatbot import config
from neo4aas.chatbot.tools import build_tools

# SICK TBT temperature sensor, present in the Lieferanten dataset.
SICK_SUFFIX = "0044dfb8196ec50444cd001e745a95cf"
BAA039 = "0173-1#02-BAA039#010"   # max ambient temperature
AAY818 = "0173-1#02-AAY818#001"   # measuring range start
AAY819 = "0173-1#02-AAY819#001"   # measuring range end
AAY820 = "0173-1#02-AAY820#001"   # max process pressure


@pytest.fixture(scope="module")
def tools():
    """The Lieferanten tool registry, or a skip.

    A configured Neo4j URI is not sufficient: every test here asserts on the SICK
    sensor, so the database must also be reachable and loaded. Checking only that
    the tool exists (as this fixture used to) turns a stopped stack into four
    ServiceUnavailable failures instead of an honest skip.
    """
    import neo4j
    from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

    repo = config.get_repo("lieferanten")
    ts = {t.name: t for t in build_tools(repo)}
    if "find_submodel_elements_by_semantic_id" not in ts:
        pytest.skip("Lieferanten repo has no Neo4j backend configured")

    driver = neo4j.GraphDatabase.driver(
        repo.neo4j_uri, auth=(repo.neo4j_user, repo.neo4j_password)
    )
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            loaded = session.run(
                "MATCH (a:AssetAdministrationShell) WHERE a.id ENDS WITH $suffix "
                "RETURN count(a) AS n",
                suffix=SICK_SUFFIX,
            ).single()["n"]
    except (ServiceUnavailable, AuthError, Neo4jError) as exc:
        pytest.skip(f"Lieferanten Neo4j unavailable at {repo.neo4j_uri}: {exc}")
    finally:
        driver.close()

    if not loaded:
        pytest.skip(
            f"Lieferanten Neo4j at {repo.neo4j_uri} is up but holds no SICK sensor "
            "(stack not loaded?)"
        )
    return ts


def _has_sick(rows, key="asset"):
    return any(r[key].endswith(SICK_SUFFIX) for r in rows)


@pytest.mark.integration
def test_renamed_tool_replaces_old(tools):
    assert "find_by_eclass_concept" not in tools
    assert "find_assets_by_semantic_criteria" in tools


@pytest.mark.integration
def test_semantic_threshold_finds_german_idshort(tools):
    """value_min on BAA039 must include SICK (125 C) — the idShort search missed it."""
    f = tools["find_submodel_elements_by_semantic_id"]
    r = f.invoke({"semantic_id": BAA039, "value_min": 100})
    assert r["count"] > 0
    assert _has_sick(r["elements"])
    # value-language-agnostic: matched element is the German idShort
    sick = next(e for e in r["elements"] if e["asset"].endswith(SICK_SUFFIX))
    assert float(sick["value"]) >= 100


@pytest.mark.integration
@pytest.mark.parametrize("sid", ["0173-1#02-BAA039", "0173-1#02-BAA039#010"])
def test_semantic_id_base_is_version_agnostic(tools, sid):
    """Versionless and versioned IRDIs must resolve to the same concept (irdi_base, not a
    naive rsplit that would strip the structural '#' of a versionless IRDI)."""
    f = tools["find_submodel_elements_by_semantic_id"]
    r = f.invoke({"semantic_id": sid, "value_min": 100})
    assert r["semantic_id"] == "0173-1#02-BAA039"
    assert _has_sick(r["elements"])


@pytest.mark.integration
def test_multi_criteria_and_matches_sick(tools):
    """All four requirements together must return the SICK sensor."""
    m = tools["find_assets_by_semantic_criteria"]
    r = m.invoke({"criteria": [
        {"semantic_id": AAY818, "op": "<=", "value": -40},
        {"semantic_id": AAY819, "op": ">=", "value": 120},
        {"semantic_id": AAY820, "op": ">=", "value": 30},
        {"semantic_id": BAA039, "op": ">=", "value": 100},
    ]})
    assert r["match_count"] > 0
    assert _has_sick(r["assets"])
    sick = next(a for a in r["assets"] if a["asset"].endswith(SICK_SUFFIX))
    assert len(sick["matched"]) == 4
    assert all(v is not None for v in sick["matched"].values())
