"""
Tests for AASConstraintChecker.

Unit tests (no Neo4j required) cover ConstraintReport behaviour and input validation.
Integration tests (marked with @pytest.mark.integration) require a running Neo4j instance
and use the `aas_client` fixture from conftest.py, which provides a clean database per test.
"""

import pytest

from neo4aas.core.validation import (
    AASConstraintChecker,
    ConstraintReport,
    ConstraintViolation,
    IDSHORT_PATTERN,
    IDSHORT_PATTERN_V30,
)


# ---------------------------------------------------------------------------
# Unit tests — no Neo4j needed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("id_short", ["MyValidIdShort01", "a_b", "Ab", "Max-Flow-Rate"])
def test_idshort_pattern_accepts_v32_conformant_names(id_short):
    """The checker validates against the current metamodel (V3.1/V3.2), whose AASd-002 is
    `^[a-zA-Z][a-zA-Z0-9_-]*[a-zA-Z0-9_]+$` — a hyphen inside the name is allowed."""
    assert IDSHORT_PATTERN.match(id_short)


@pytest.mark.parametrize("id_short", ["1invalid", "trailing-", "with space", "_x", "A"])
def test_idshort_pattern_rejects_non_conformant_names(id_short):
    """No leading non-letter, no trailing hyphen, no foreign characters, ≥2 characters."""
    assert not IDSHORT_PATTERN.match(id_short)


@pytest.mark.parametrize("id_short", ["Max-Flow-Rate", "1invalid", "with space"])
def test_v30_pattern_is_stricter(id_short):
    """V3.0 (what basyx implements) forbids the hyphen the newer versions permit."""
    assert not IDSHORT_PATTERN_V30.match(id_short)


def test_report_is_compliant_when_empty():
    report = ConstraintReport()
    assert report.is_compliant()


def test_report_is_not_compliant_with_violation():
    report = ConstraintReport(
        violations=[ConstraintViolation("AASd-002", "bad idShort", {"value": "1x"})],
        checked_constraints=["AASd-002"],
    )
    assert not report.is_compliant()


def test_report_summary_mentions_violation_count():
    report = ConstraintReport(
        violations=[ConstraintViolation("AASd-002", "bad idShort", {})],
        checked_constraints=["AASd-002"],
    )
    s = report.summary()
    assert "1 violation" in s
    assert "AASd-002" in s


def test_report_summary_zero_violations():
    report = ConstraintReport(checked_constraints=["AASd-002"])
    s = report.summary()
    assert "0 violation" in s


def test_checker_raises_on_unknown_constraint_id(aas_client):
    checker = AASConstraintChecker(aas_client)
    with pytest.raises(ValueError, match="Unknown constraint IDs"):
        checker.check(["AASd-999"])


def test_checker_raises_on_multiple_unknown_ids(aas_client):
    checker = AASConstraintChecker(aas_client)
    with pytest.raises(ValueError):
        checker.check(["AASd-999", "AASd-000"])


# The checker only touches the client when a check runs, so these need no database.

def test_checker_rejects_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported metamodel version"):
        AASConstraintChecker(None, version="3.3")


_VERSION_ONLY = {"AASd-090", "AASd-120", "AASd-137", "AASd-138"}


@pytest.mark.parametrize("version, own", [
    (None, set()),  # unchanged default: the V3.1 rule set
    ("3.0", {"AASd-090", "AASd-120"}),
    ("3.1", set()),
    ("3.2", {"AASd-137", "AASd-138"}),
])
def test_constraint_set_follows_version(version, own):
    ids = set(AASConstraintChecker(None, version=version).constraint_ids)
    assert len(ids) == 25 + len(own)
    assert ids & _VERSION_ONLY == own


@pytest.mark.parametrize("version, cid", [
    (None, "AASd-120"),
    ("3.1", "AASd-090"),
    ("3.2", "AASd-120"),
    ("3.0", "AASd-137"),
    ("3.1", "AASd-138"),
])
def test_constraint_of_another_version_is_rejected(version, cid):
    with pytest.raises(ValueError, match="do not apply"):
        AASConstraintChecker(None, version=version).check([cid])


@pytest.mark.parametrize("version, pattern", [
    (None, IDSHORT_PATTERN),
    ("3.0", IDSHORT_PATTERN_V30),
    ("3.1", IDSHORT_PATTERN),
    ("3.2", IDSHORT_PATTERN),
])
def test_idshort_pattern_follows_version(version, pattern):
    assert AASConstraintChecker(None, version=version)._idshort_pattern() is pattern


# ---------------------------------------------------------------------------
# Integration test helpers
# ---------------------------------------------------------------------------

def _check(client, env: dict, constraint_ids: list[str]) -> ConstraintReport:
    client.upload_json(env)
    return AASConstraintChecker(client).check(constraint_ids)


# ---------------------------------------------------------------------------
# Minimal valid environment (should have zero violations for all constraints)
# ---------------------------------------------------------------------------

_VALID_ENV = {
    "assetAdministrationShells": [
        {
            "modelType": "AssetAdministrationShell",
            "id": "urn:test:valid-aas",
            "idShort": "ValidAas",
            "administration": {"version": "1", "revision": "0"},
            "assetInformation": {
                "assetKind": "Instance",
                "globalAssetId": "urn:test:valid-asset",
            },
        }
    ],
    "submodels": [
        {
            "modelType": "Submodel",
            "id": "urn:test:valid-sm",
            "idShort": "ValidSm",
            "kind": "Instance",
            "submodelElements": [
                {
                    "modelType": "Property",
                    "idShort": "MyProp",
                    "valueType": "xs:string",
                    "value": "hello",
                    "semanticId": {
                        "type": "ExternalReference",
                        "keys": [{"type": "GlobalReference", "value": "urn:sem:1"}],
                    },
                }
            ],
        }
    ],
}


@pytest.mark.integration
def test_valid_environment_has_no_violations(aas_client):
    client = aas_client
    client.upload_json(_VALID_ENV)
    report = AASConstraintChecker(client).check_all()
    assert report.is_compliant(), f"Expected no violations, got:\n{report.summary()}"
    assert len(report.checked_constraints) == 25


# ---------------------------------------------------------------------------
# AASd-002 — idShort format
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd002_violation_starts_with_digit(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:1",
            "idShort": "1invalid",
        }]
    }
    report = _check(aas_client, env, ["AASd-002"])
    assert not report.is_compliant()
    assert all(v.constraint_id == "AASd-002" for v in report.violations)
    assert any(v.details["value"] == "1invalid" for v in report.violations)


@pytest.mark.integration
def test_aasd002_violation_ends_with_hyphen(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:2",
            "idShort": "trailing-",
        }]
    }
    report = _check(aas_client, env, ["AASd-002"])
    assert not report.is_compliant()


@pytest.mark.integration
def test_aasd002_no_violation_for_valid_id_short(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:3",
            "idShort": "MyValidIdShort01",
        }]
    }
    report = _check(aas_client, env, ["AASd-002"])
    assert report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-005 — revision requires version
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd005_violation_revision_without_version(aas_client):
    env = {
        "assetAdministrationShells": [{
            "modelType": "AssetAdministrationShell",
            "id": "urn:t:aas1",
            "idShort": "TestAas",
            "administration": {"revision": "1"},
            "assetInformation": {"assetKind": "Instance", "globalAssetId": "urn:t:asset1"},
        }]
    }
    report = _check(aas_client, env, ["AASd-005"])
    assert not report.is_compliant()
    assert report.violations[0].details["revision_value"] == "1"


@pytest.mark.integration
def test_aasd005_no_violation_with_version_and_revision(aas_client):
    env = {
        "assetAdministrationShells": [{
            "modelType": "AssetAdministrationShell",
            "id": "urn:t:aas2",
            "idShort": "TestAas2",
            "administration": {"version": "2", "revision": "1"},
            "assetInformation": {"assetKind": "Instance", "globalAssetId": "urn:t:asset2"},
        }]
    }
    report = _check(aas_client, env, ["AASd-005"])
    assert report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-014 — SelfManagedEntity
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd014_violation_self_managed_no_asset_id(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm14",
            "idShort": "Sm14",
            "submodelElements": [{
                "modelType": "Entity",
                "idShort": "MyEntity",
                "entityType": "SelfManagedEntity",
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-014"])
    assert not report.is_compliant()
    assert report.violations[0].details["id_short"] == "MyEntity"


@pytest.mark.integration
def test_aasd014_no_violation_self_managed_with_specific_asset_id(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm14c",
            "idShort": "Sm14c",
            "submodelElements": [{
                "modelType": "Entity",
                "idShort": "MyEntity",
                "entityType": "SelfManagedEntity",
                "specificAssetIds": [{"name": "serialNumber", "value": "1234"}],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-014"])
    assert report.is_compliant(), report.summary()


@pytest.mark.integration
def test_aasd014_no_violation_co_managed_without_asset_id(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm14b",
            "idShort": "Sm14b",
            "submodelElements": [{
                "modelType": "Entity",
                "idShort": "MyEntity",
                "entityType": "CoManagedEntity",
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-014"])
    assert report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-021 — Qualifier type uniqueness
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd021_violation_duplicate_qualifier_type(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm21",
            "idShort": "Sm21",
            "submodelElements": [{
                "modelType": "Property",
                "idShort": "PropWithQuals",
                "valueType": "xs:string",
                "qualifiers": [
                    {"kind": "ConceptQualifier", "type": "SameType", "valueType": "xs:string"},
                    {"kind": "ConceptQualifier", "type": "SameType", "valueType": "xs:int"},
                ],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-021"])
    assert not report.is_compliant()
    assert report.violations[0].details["duplicate_qualifier_type"] == "SameType"


# ---------------------------------------------------------------------------
# AASd-022 — idShort uniqueness within namespace
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd022_violation_duplicate_id_short_in_collection(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm22",
            "idShort": "Sm22",
            "submodelElements": [
                {"modelType": "Property", "idShort": "Dup", "valueType": "xs:string"},
                {"modelType": "Property", "idShort": "Dup", "valueType": "xs:int"},
            ]
        }]
    }
    report = _check(aas_client, env, ["AASd-022"])
    assert not report.is_compliant()
    assert report.violations[0].details["duplicate_id_short"] == "Dup"


# ---------------------------------------------------------------------------
# AASd-077 — Extension name uniqueness
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd077_violation_duplicate_extension_name(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm77",
            "idShort": "Sm77",
            "extensions": [
                {"name": "SameName", "valueType": "xs:string"},
                {"name": "SameName", "valueType": "xs:int"},
            ],
        }]
    }
    report = _check(aas_client, env, ["AASd-077"])
    assert not report.is_compliant()
    assert report.violations[0].details["duplicate_extension_name"] == "SameName"


# ---------------------------------------------------------------------------
# AASd-108 — SubmodelElementList children type
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd108_violation_wrong_child_type(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm108",
            "idShort": "Sm108",
            "submodelElements": [{
                "modelType": "SubmodelElementList",
                "idShort": "MyList",
                "typeValueListElement": "Property",
                "value": [
                    {"modelType": "Blob", "idShort": "NotAProperty", "contentType": "text/plain"},
                ],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-108"])
    assert not report.is_compliant()
    assert report.violations[0].details["expected_type"] == "Property"


# ---------------------------------------------------------------------------
# AASd-109 — valueTypeListElement
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd109_violation_missing_value_type_list_element(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm109a",
            "idShort": "Sm109a",
            "submodelElements": [{
                "modelType": "SubmodelElementList",
                "idShort": "PropList",
                "typeValueListElement": "Property",
                # valueTypeListElement intentionally omitted
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-109"])
    assert not report.is_compliant()
    assert any(v.details.get("issue") == "missing_valueTypeListElement" for v in report.violations)


@pytest.mark.integration
def test_aasd109_violation_wrong_child_value_type(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm109b",
            "idShort": "Sm109b",
            "submodelElements": [{
                "modelType": "SubmodelElementList",
                "idShort": "PropList",
                "typeValueListElement": "Property",
                "valueTypeListElement": "xs:string",
                "value": [
                    {"modelType": "Property", "valueType": "xs:int"},
                ],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-109"])
    assert not report.is_compliant()
    assert any(v.details.get("issue") == "wrong_child_value_type" for v in report.violations)


# ---------------------------------------------------------------------------
# AASd-117 — idShort required for non-list children
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd117_violation_missing_id_short(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm117",
            "idShort": "Sm117",
            "submodelElements": [{
                "modelType": "Property",
                # idShort intentionally omitted
                "valueType": "xs:string",
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-117"])
    assert not report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-118 — supplementalSemanticIds requires semanticId
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd118_violation_supplemental_without_main(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm118",
            "idShort": "Sm118",
            "supplementalSemanticIds": [
                {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": "urn:s:1"}]}
            ],
            # semanticId intentionally omitted
        }]
    }
    report = _check(aas_client, env, ["AASd-118"])
    assert not report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-119 — TemplateQualifier requires Template kind
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd119_violation_template_qualifier_on_instance(aas_client):
    # AASd-119 fires when the *qualified element itself* has HasKind and kind != Template.
    # Submodel stores kind explicitly; SubmodelElements do not (default is Instance but
    # it is not persisted in the graph, so the check cannot fire on them).
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm119",
            "idShort": "Sm119",
            "kind": "Instance",
            "qualifiers": [{"kind": "TemplateQualifier", "type": "MyQual", "valueType": "xs:string"}],
        }]
    }
    report = _check(aas_client, env, ["AASd-119"])
    assert not report.is_compliant()
    assert report.violations[0].details["actual_kind"] == "Instance"


# ---------------------------------------------------------------------------
# AASd-121 — Reference first key type
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd121_violation_first_key_not_globally_identifiable(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm121",
            "idShort": "Sm121",
            "submodelElements": [{
                "modelType": "Property",
                "idShort": "Prop121",
                "valueType": "xs:string",
                "semanticId": {
                    "type": "ModelReference",
                    "keys": [
                        {"type": "Blob", "value": "urn:bad:first-key"},
                    ],
                },
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-121"])
    assert not report.is_compliant()
    assert report.violations[0].details["first_key_type"] == "Blob"


# ---------------------------------------------------------------------------
# AASd-122 — ExternalReference first key
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd122_violation_external_ref_wrong_first_key(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm122",
            "idShort": "Sm122",
            "semanticId": {
                "type": "ExternalReference",
                "keys": [{"type": "Submodel", "value": "urn:bad"}],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-122"])
    assert not report.is_compliant()
    assert report.violations[0].details["first_key_type"] == "Submodel"


# ---------------------------------------------------------------------------
# AASd-123 — ModelReference first key
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd123_violation_model_ref_wrong_first_key(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm123",
            "idShort": "Sm123",
            "semanticId": {
                "type": "ModelReference",
                "keys": [{"type": "GlobalReference", "value": "urn:bad"}],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-123"])
    assert not report.is_compliant()
    assert report.violations[0].details["first_key_type"] == "GlobalReference"


# ---------------------------------------------------------------------------
# AASd-124 — ExternalReference last key
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd124_violation_external_ref_wrong_last_key(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm124",
            "idShort": "Sm124",
            "semanticId": {
                "type": "ExternalReference",
                "keys": [{"type": "GlobalReference", "value": "urn:ok"}, {"type": "Submodel", "value": "urn:bad"}],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-124"])
    assert not report.is_compliant()
    assert report.violations[0].details["last_key_type"] == "Submodel"


# ---------------------------------------------------------------------------
# AASd-125 — ModelReference following keys in FragmentKeys
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd125_violation_following_key_not_in_fragment_keys(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm125",
            "idShort": "Sm125",
            "semanticId": {
                "type": "ModelReference",
                "keys": [
                    {"type": "Submodel", "value": "urn:sm"},
                    {"type": "AssetAdministrationShell", "value": "urn:bad"},
                ],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-125"])
    assert not report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-126 — FragmentReference must be last key
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd126_violation_fragment_ref_not_last(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm126",
            "idShort": "Sm126",
            "semanticId": {
                "type": "ModelReference",
                "keys": [
                    {"type": "Submodel", "value": "urn:sm"},
                    {"type": "FragmentReference", "value": "frag"},
                    {"type": "Property", "value": "urn:prop"},
                ],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-126"])
    assert not report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-127 — FragmentReference preceded by File or Blob
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd127_violation_fragment_ref_not_preceded_by_file_or_blob(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm127",
            "idShort": "Sm127",
            "semanticId": {
                "type": "ModelReference",
                "keys": [
                    {"type": "Submodel", "value": "urn:sm"},
                    {"type": "Property", "value": "urn:prop"},
                    {"type": "FragmentReference", "value": "frag"},
                ],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-127"])
    assert not report.is_compliant()
    assert report.violations[0].details["preceding_key_type"] == "Property"


# ---------------------------------------------------------------------------
# AASd-128 — Key after SubmodelElementList must be numeric
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd128_violation_non_numeric_key_after_list(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm128",
            "idShort": "Sm128",
            "semanticId": {
                "type": "ModelReference",
                "keys": [
                    {"type": "Submodel", "value": "urn:sm"},
                    {"type": "SubmodelElementList", "value": "MyList"},
                    {"type": "Property", "value": "notAnInteger"},
                ],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-128"])
    assert not report.is_compliant()
    bad = report.violations[0].details["bad_entries"][0]
    assert bad["key_value"] == "notAnInteger"


# ---------------------------------------------------------------------------
# AASd-129 — TemplateQualifier requires Template Submodel
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd129_violation_template_qualifier_under_instance_submodel(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm129",
            "idShort": "Sm129",
            "kind": "Instance",
            "submodelElements": [{
                "modelType": "Property",
                "idShort": "TplProp",
                "valueType": "xs:string",
                "qualifiers": [{"kind": "TemplateQualifier", "type": "TQ", "valueType": "xs:string"}],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-129"])
    assert not report.is_compliant()


@pytest.mark.integration
def test_aasd129_no_violation_template_qualifier_under_template_submodel(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm129ok",
            "idShort": "Sm129ok",
            "kind": "Template",
            "submodelElements": [{
                "modelType": "Property",
                "idShort": "TplProp",
                "valueType": "xs:string",
                "qualifiers": [{"kind": "TemplateQualifier", "type": "TQ", "valueType": "xs:string"}],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-129"])
    assert report.is_compliant()


def _template_qualified(id_short: str) -> dict:
    return {
        "modelType": "Property",
        "idShort": id_short,
        "valueType": "xs:string",
        "qualifiers": [{"kind": "TemplateQualifier", "type": "TQ", "valueType": "xs:string"}],
    }


@pytest.mark.integration
@pytest.mark.parametrize("container", [
    {
        "modelType": "Operation",
        "idShort": "Op",
        "inputVariables": [{"value": _template_qualified("InArg")}],
    },
    {
        "modelType": "AnnotatedRelationshipElement",
        "idShort": "Rel",
        "first": {"type": "ExternalReference",
                  "keys": [{"type": "GlobalReference", "value": "urn:t:a"}]},
        "second": {"type": "ExternalReference",
                   "keys": [{"type": "GlobalReference", "value": "urn:t:b"}]},
        "annotations": [_template_qualified("Note")],
    },
])
def test_aasd129_no_violation_nested_in_template_submodel(aas_client, container):
    # Operation variables and annotations are part of the template too.
    env = {"submodels": [{
        "modelType": "Submodel",
        "id": "urn:t:sm129nested",
        "idShort": "Sm129nested",
        "kind": "Template",
        "submodelElements": [container],
    }]}
    report = _check(aas_client, env, ["AASd-129"])
    assert report.is_compliant(), report.summary()


# ---------------------------------------------------------------------------
# AASd-131 — AssetInformation must have globalAssetId or specificAssetIds
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd131_violation_asset_information_no_ids(aas_client):
    env = {
        "assetAdministrationShells": [{
            "modelType": "AssetAdministrationShell",
            "id": "urn:t:aas131",
            "idShort": "Aas131",
            "assetInformation": {
                "assetKind": "Instance",
                # globalAssetId omitted, no specificAssetIds
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-131"])
    assert not report.is_compliant()


# ---------------------------------------------------------------------------
# AASd-133 — SpecificAssetId.externalSubjectId must be ExternalReference
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd133_violation_external_subject_id_is_model_reference(aas_client):
    env = {
        "assetAdministrationShells": [{
            "modelType": "AssetAdministrationShell",
            "id": "urn:t:aas133",
            "idShort": "Aas133",
            "assetInformation": {
                "assetKind": "Instance",
                "globalAssetId": "urn:t:asset133",
                "specificAssetIds": [{
                    "name": "myId",
                    "value": "myVal",
                    "externalSubjectId": {
                        "type": "ModelReference",
                        "keys": [{"type": "Submodel", "value": "urn:sm"}],
                    },
                }],
            },
        }]
    }
    report = _check(aas_client, env, ["AASd-133"])
    assert not report.is_compliant()
    assert report.violations[0].details["actual_ref_type"] == "ModelReference"


# ---------------------------------------------------------------------------
# AASd-134 — Operation variable idShorts must be unique
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_aasd134_violation_duplicate_variable_id_short(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm134",
            "idShort": "Sm134",
            "submodelElements": [{
                "modelType": "Operation",
                "idShort": "MyOp",
                "inputVariables": [
                    {"value": {"modelType": "Property", "idShort": "DupParam", "valueType": "xs:string"}}
                ],
                "outputVariables": [
                    {"value": {"modelType": "Property", "idShort": "DupParam", "valueType": "xs:int"}}
                ],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-134"])
    assert not report.is_compliant()
    assert report.violations[0].details["duplicate_id_short"] == "DupParam"


@pytest.mark.integration
def test_aasd134_no_violation_unique_variable_id_shorts(aas_client):
    env = {
        "submodels": [{
            "modelType": "Submodel",
            "id": "urn:t:sm134ok",
            "idShort": "Sm134ok",
            "submodelElements": [{
                "modelType": "Operation",
                "idShort": "MyOp",
                "inputVariables": [
                    {"value": {"modelType": "Property", "idShort": "InputA", "valueType": "xs:string"}}
                ],
                "outputVariables": [
                    {"value": {"modelType": "Property", "idShort": "OutputB", "valueType": "xs:int"}}
                ],
            }]
        }]
    }
    report = _check(aas_client, env, ["AASd-134"])
    assert report.is_compliant()


# ---------------------------------------------------------------------------
# Metamodel version
# ---------------------------------------------------------------------------

def _check_version(client, env: dict, constraint_ids: list[str], version) -> ConstraintReport:
    client.upload_json(env)
    return AASConstraintChecker(client, version=version).check(constraint_ids)


@pytest.mark.integration
@pytest.mark.parametrize("version, expected", [("3.0", 27), ("3.1", 25), ("3.2", 27)])
def test_valid_environment_has_no_violations_per_version(aas_client, version, expected):
    aas_client.upload_json(_VALID_ENV)
    report = AASConstraintChecker(aas_client, version=version).check_all()
    assert report.is_compliant(), f"Expected no violations, got:\n{report.summary()}"
    assert len(report.checked_constraints) == expected


@pytest.mark.integration
@pytest.mark.parametrize("id_short, version, compliant", [
    ("Max-Flow", "3.0", False),  # V3.0 forbids the hyphen
    ("Max-Flow", "3.1", True),
    ("X", "3.0", True),          # V3.1/V3.2 require two characters
    ("X", "3.2", False),
])
def test_aasd002_pattern_depends_on_version(aas_client, id_short, version, compliant):
    env = {"submodels": [{"modelType": "Submodel", "id": "urn:t:v002", "idShort": id_short}]}
    report = _check_version(aas_client, env, ["AASd-002"], version)
    assert report.is_compliant() is compliant


def _submodel_with(element: dict) -> dict:
    return {"submodels": [{
        "modelType": "Submodel",
        "id": "urn:t:v30",
        "idShort": "SmV30",
        "submodelElements": [element],
    }]}


@pytest.mark.integration
def test_aasd090_violation_unknown_category(aas_client):
    env = _submodel_with({
        "modelType": "Property",
        "idShort": "Temp",
        "category": "MEASUREMENT",
        "valueType": "xs:double",
    })
    report = _check_version(aas_client, env, ["AASd-090"], "3.0")
    assert not report.is_compliant()
    assert report.violations[0].details["category"] == "MEASUREMENT"


@pytest.mark.integration
@pytest.mark.parametrize("category", ["CONSTANT", "PARAMETER", "VARIABLE"])
def test_aasd090_no_violation_allowed_category(aas_client, category):
    env = _submodel_with({
        "modelType": "Property",
        "idShort": "Temp",
        "category": category,
        "valueType": "xs:double",
    })
    report = _check_version(aas_client, env, ["AASd-090"], "3.0")
    assert report.is_compliant()


@pytest.mark.integration
def test_aasd120_violation_list_child_with_id_short(aas_client):
    env = _submodel_with({
        "modelType": "SubmodelElementList",
        "idShort": "Readings",
        "typeValueListElement": "Property",
        "valueTypeListElement": "xs:int",
        "value": [{"modelType": "Property", "idShort": "First", "valueType": "xs:int"}],
    })
    report = _check_version(aas_client, env, ["AASd-120"], "3.0")
    assert not report.is_compliant()
    assert report.violations[0].details["id_short"] == "First"
    assert report.violations[0].details["list_id_short"] == "Readings"


@pytest.mark.integration
def test_aasd120_no_violation_list_child_without_id_short(aas_client):
    env = _submodel_with({
        "modelType": "SubmodelElementList",
        "idShort": "Readings",
        "typeValueListElement": "Property",
        "valueTypeListElement": "xs:int",
        "value": [{"modelType": "Property", "valueType": "xs:int"}],
    })
    report = _check_version(aas_client, env, ["AASd-120"], "3.0")
    assert report.is_compliant()


@pytest.mark.integration
@pytest.mark.parametrize("version, compliant", [("3.0", False), ("3.1", True), ("3.2", True)])
def test_aasd014_co_managed_with_asset_id_depends_on_version(aas_client, version, compliant):
    env = _submodel_with({
        "modelType": "Entity",
        "idShort": "Part",
        "entityType": "CoManagedEntity",
        "globalAssetId": "urn:t:asset:part",
    })
    report = _check_version(aas_client, env, ["AASd-014"], version)
    assert report.is_compliant() is compliant


@pytest.mark.integration
@pytest.mark.parametrize("version, compliant", [("3.1", False), ("3.2", True)])
def test_aasd129_operation_variable_exception_in_v32(aas_client, version, compliant):
    env = {"submodels": [{
        "modelType": "Submodel",
        "id": "urn:t:sm129op",
        "idShort": "Sm129op",
        "kind": "Instance",
        "submodelElements": [{
            "modelType": "Operation",
            "idShort": "Op",
            "inputVariables": [{"value": _template_qualified("InArg")}],
        }],
    }]}
    report = _check_version(aas_client, env, ["AASd-129"], version)
    assert report.is_compliant() is compliant


def _external_ref(*key_types: str) -> dict:
    return {
        "type": "ExternalReference",
        "keys": [{"type": t, "value": f"urn:t:{i}"} for i, t in enumerate(key_types)],
    }


@pytest.mark.integration
@pytest.mark.parametrize("key_types, compliant", [
    (("GlobalReference",), True),
    (("GlobalReference", "FragmentReference"), True),
    (("GlobalReference", "Property"), False),
    (("GlobalReference", "Submodel", "FragmentReference"), False),
])
def test_aasd137_external_reference_key_types(aas_client, key_types, compliant):
    env = _submodel_with({
        "modelType": "ReferenceElement",
        "idShort": "Link",
        "value": _external_ref(*key_types),
    })
    report = _check_version(aas_client, env, ["AASd-137"], "3.2")
    assert report.is_compliant() is compliant, report.summary()


def _list_of(n: int) -> dict:
    return {
        "modelType": "SubmodelElementList",
        "idShort": "Readings",
        "typeValueListElement": "Property",
        "valueTypeListElement": "xs:int",
        "value": [{"modelType": "Property", "valueType": "xs:int"} for _ in range(n)],
    }


@pytest.mark.integration
@pytest.mark.parametrize("kind, n, compliant", [
    ("Template", 1, True),
    ("Template", 2, False),
    ("Template", 0, False),
    ("Instance", 2, True),  # outside templates and OperationVariables, any size is fine
])
def test_aasd138_list_size_in_template(aas_client, kind, n, compliant):
    env = {"submodels": [{
        "modelType": "Submodel",
        "id": "urn:t:sm138",
        "idShort": "Sm138",
        "kind": kind,
        "submodelElements": [{
            "modelType": "SubmodelElementCollection",
            "idShort": "Group",
            "value": [_list_of(n)],
        }],
    }]}
    report = _check_version(aas_client, env, ["AASd-138"], "3.2")
    assert report.is_compliant() is compliant, report.summary()


@pytest.mark.integration
def test_aasd138_violation_list_in_operation_variable(aas_client):
    env = _submodel_with({
        "modelType": "Operation",
        "idShort": "Op",
        "outputVariables": [{"value": _list_of(2)}],
    })
    report = _check_version(aas_client, env, ["AASd-138"], "3.2")
    assert not report.is_compliant()
    assert report.violations[0].details["element_count"] == 2
