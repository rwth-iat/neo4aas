"""Version-agnostic ECLASS/IRDI discovery.

Unit tests for `id_base` (no Neo4j) plus integration tests that the IRDI base
is stored/indexed at import and lets the same property be discovered across
ECLASS versions.
"""

import pytest

from neo4aas.core.utils import id_base, is_irdi


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
        # Wrapped IRDIs: forms that *are* an ECLASS IRDI written inside a wrapper the
        # join key must see through. The IDTA Battery Passport template wraps them in a
        # URN; the CDP URL turns up with a stray space and in already-'#' form.
        ("urn:irdi:0173-1#02-AAN920#003", "0173-1#02-AAN920"),
        ("URN:IRDI:0173-1#02-AAN920#003", "0173-1#02-AAN920"),   # scheme is case-insensitive
        ("urn:irdi:0112/2///61987#ABN590#002", "0112/2///61987#ABN590"),
        ("https://api.eclass-cdp.com/0173-1#02-ABL775#001", "0173-1#02-ABL775"),
        ("https://api.eclass-cdp.com/ 0173-1-02-ABK163-002", "0173-1#02-ABK163"),
        # Surrounding whitespace: nine ECLASS IRDIs in the corpus are shipped padded
        # (' 0173-1#02-AAN468#008' on a Language property). Trimming invents nothing.
        (" 0173-1#02-AAN468#008", "0173-1#02-AAN468"),
        ("0173-1#02-AAN468#008\n", "0173-1#02-AAN468"),
        ("\t0112/2///61987#ABN590#002 ", "0112/2///61987#ABN590"),
        # Not wrappers, and deliberately left alone — see test_scope_of_normalisation.
        ("urn:samm:io.admin-shell.idta.batterypass.technical_data:1.0.0#batteryMass",
         "urn:samm:io.admin-shell.idta.batterypass.technical_data:1.0.0#batteryMass"),
        ("https://example.com/foo#bar", "https://example.com/foo#bar"),  # not IRDI
        ("https://admin-shell.io/x#123", "https://admin-shell.io/x#123"),  # no ICD prefix
        ("", ""),
    ],
)
def test_id_base(value, expected):
    assert id_base(value) == expected


def test_id_base_collapses_iec_cdd_versions():
    assert id_base("0112/2///61987#ABN590#001") == id_base("0112/2///61987#ABN590#004")


def test_id_base_collapses_versions():
    assert id_base("0173-1#02-AAO677#001") == id_base("0173-1#02-AAO677#007")


@pytest.mark.parametrize("value", [
    # A misspelled wrapper. Repairing it would be *inventing* an identifier — the same
    # rule that drops IEC CDD's prefix-less IRDIs instead of guessing their ICD.
    "urn:iridi:0173-1#02-ABL851#001",
    "urn:idi:0173-1#02-ABL854#001",
    "urn:samurn:samm:io.admin-shell.idta.generic.technical_data:2.0.0#manufacturerOrderCode",
    # An Eclipse SAMM aspect-model id: a different identifier system, not an IRDI at all.
    "urn:samm:io.admin-shell.idta.handover_documentation:2.0.0#DocumentIdentifier",
    "https://api.eclass-cdp.com/0173-1-02-ABI501-003/0173-1-01-AHF580-003",
])
def test_scope_of_normalisation(value):
    """Forms deliberately *not* normalised. These guard the scope decision."""
    assert id_base(value) == value
    assert not is_irdi(value)


def test_a_compound_irdi_is_not_collapsed_to_its_head():
    """``A~0/B`` names B *in the context of* A, so the head alone is a different
    concept. The trailing version is stripped as on any IRDI — that is unchanged —
    but the pair is never reduced to one member."""
    compound = "0173-1#02-ABI503#003~0/0173-1#01-AHF582#003"
    assert id_base(compound) == "0173-1#02-ABI503#003~0/0173-1#01-AHF582"
    assert id_base(compound) != id_base("0173-1#02-ABI503#003")


def test_a_padded_irdi_and_its_trimmed_form_are_one_base():
    """Whitespace is never part of an identifier, so it must not split a concept."""
    assert id_base(" 0173-1#02-AAN468#008") == id_base("0173-1#02-AAN468#012")
    assert is_irdi("  0173-1#02-AAN468#008  ")


def test_trimming_does_not_repair_a_malformed_icd():
    """Only surrounding whitespace goes. A wrong ICD stays wrong — '0173a-1' and
    '173-1' are shipped by real files and are not repairable without guessing."""
    for value in (" 0173a-1#02-ABK288#002 ", " 173-1#02-AAW627#001 "):
        assert not is_irdi(value)
        assert id_base(value) == value.strip()


def test_a_wrapped_irdi_and_its_plain_form_are_one_base():
    """The join property: the wrapper must not split a concept in two."""
    assert id_base("urn:irdi:0173-1#02-AAN920#003") == id_base("0173-1#02-AAN920#007")


@pytest.mark.parametrize("value", [
    "0173-1#02-AAN920#003",
    "urn:irdi:0173-1#02-AAN920#003",
    "https://api.eclass-cdp.com/0173-1-01-AHX837-002",
    "https://api.eclass-cdp.com/0173-1#02-ABL775#001",
    "0112/2///61987#ABN590#002",
])
def test_is_irdi_agrees_with_id_base_about_wrappers(value):
    """The two must not disagree: one decides what joins, the other what is rejected."""
    assert is_irdi(value)


def test_id_base_cdp_url_matches_plain_irdi():
    assert id_base("https://api.eclass-cdp.com/0173-1-01-AHX837-002") == id_base(
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
def test_id_base_props_stripped_on_export(_eclass_env):
    sm = _eclass_env.get_identifiable("urn:sm/v1")
    sem = sm["submodelElements"][0]["semanticId"]
    assert "target_id_base" not in sem and "target_id" not in sem
