"""Version-agnostic ECLASS/IRDI discovery.

Unit tests for `irdi_base` (no Neo4j) plus integration tests that the IRDI base
is stored/indexed at import and lets the same property be discovered across
ECLASS versions.
"""

import pytest

from neo4aas.core.utils import irdi_base


# ---------------------------------------------------------------------------
# Unit tests (no Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0173-1#02-AAO677#002", "0173-1#02-AAO677"),
        ("0173-1#02-AAO677#001", "0173-1#02-AAO677"),  # same base, different version
        ("0173-1#02-AAO677#12", "0173-1#02-AAO677"),    # multi/var-length version
        ("0173-1#02-AAO677", "0173-1#02-AAO677"),        # no version -> unchanged
        # ECLASS CDP URL: dash-encoded IRDI -> canonical base (matches plain IRDI)
        ("https://api.eclass-cdp.com/0173-1-01-AHX837-002", "0173-1#01-AHX837"),
        ("http://api.eclass-cdp.com/0173-1-02-AAO677-002", "0173-1#02-AAO677"),
        # IEC CDD IRDIs use '/' after the ICD, not '-'; they carry the same trailing
        # version and must be stripped the same way. Real vendor data references IEC CDD
        # concepts alongside ECLASS ones.
        ("0112/2///61360_4#AAF120#001", "0112/2///61360_4#AAF120"),
        ("0112/2///61987#ABN590#002", "0112/2///61987#ABN590"),
        ("0112/2///61987#ABN590", "0112/2///61987#ABN590"),  # no version -> unchanged
        ("https://example.com/foo#bar", "https://example.com/foo#bar"),  # not IRDI
        ("https://admin-shell.io/x#123", "https://admin-shell.io/x#123"),  # no ICD prefix
        ("", ""),
    ],
)
def test_irdi_base(value, expected):
    assert irdi_base(value) == expected


def test_irdi_base_collapses_iec_cdd_versions():
    assert irdi_base("0112/2///61987#ABN590#001") == irdi_base("0112/2///61987#ABN590#004")


def test_irdi_base_collapses_versions():
    assert irdi_base("0173-1#02-AAO677#001") == irdi_base("0173-1#02-AAO677#007")


def test_irdi_base_cdp_url_matches_plain_irdi():
    assert irdi_base("https://api.eclass-cdp.com/0173-1-01-AHX837-002") == irdi_base(
        "0173-1#01-AHX837#002"
    )


# ---------------------------------------------------------------------------
# Integration tests (require live Neo4j)
# ---------------------------------------------------------------------------

_BASE = "0173-1#02-AAO677"


def _submodel_with_semantic_id(sm_id, prop_id_short, irdi):
    return {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": sm_id.split("/")[-1],
        "submodelElements": [
            {
                "modelType": "Property",
                "idShort": prop_id_short,
                "valueType": "xs:string",
                "value": "x",
                "semanticId": {
                    "type": "ExternalReference",
                    "keys": [{"type": "GlobalReference", "value": irdi}],
                },
            }
        ],
    }


@pytest.fixture
def _eclass_env(aas_client):
    """Two submodels whose Property shares one ECLASS property across two versions."""
    env = {
        "assetAdministrationShells": [],
        "submodels": [
            _submodel_with_semantic_id("urn:sm/v1", "Width", f"{_BASE}#001"),
            _submodel_with_semantic_id("urn:sm/v2", "Width", f"{_BASE}#002"),
        ],
        "conceptDescriptions": [
            {"modelType": "ConceptDescription", "id": f"{_BASE}#001", "idShort": "Width"},
            {"modelType": "ConceptDescription", "id": f"{_BASE}#002", "idShort": "Width"},
        ],
    }
    aas_client.upload_json(env)
    return aas_client


@pytest.mark.integration
def test_version_agnostic_match_finds_all_versions(_eclass_env):
    hits = _eclass_env.find_referables_by_semantic_id(f"{_BASE}#001")
    semantic_ids = sorted(h["semanticId"] for h in hits)
    assert semantic_ids == [f"{_BASE}#001", f"{_BASE}#002"]


@pytest.mark.integration
def test_exact_match_finds_only_one_version(_eclass_env):
    hits = _eclass_env.find_referables_by_semantic_id(
        f"{_BASE}#002", version_agnostic=False
    )
    assert [h["semanticId"] for h in hits] == [f"{_BASE}#002"]


@pytest.mark.integration
def test_concept_description_id_base_matches_across_versions(_eclass_env):
    rows = _eclass_env.execute_clause(
        "MATCH (c:ConceptDescription) WHERE c.id_base = $b RETURN c.id AS id",
        params={"b": _BASE},
    )
    assert sorted(r["id"] for r in rows) == [f"{_BASE}#001", f"{_BASE}#002"]


@pytest.mark.integration
def test_irdi_base_props_stripped_on_export(_eclass_env):
    sm = _eclass_env.get_identifiable("urn:sm/v1")
    sem = sm["submodelElements"][0]["semanticId"]
    assert "target_id_base" not in sem and "target_id" not in sem
