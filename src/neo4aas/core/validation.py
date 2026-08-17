"""
AAS Constraint Checker — validates AAS data in Neo4j against the AAS specification constraints.

Implemented constraints
-----------------------
AASd-002  idShort format regex
AASd-005  revision requires version in AdministrativeInformation
AASd-014  SelfManagedEntity must have globalAssetId or specificAssetId
AASd-021  Qualifier type uniqueness per Qualifiable
AASd-022  idShort uniqueness within parent namespace
AASd-077  Extension name uniqueness within parent
AASd-107  SubmodelElementList child semanticId must match semanticIdListElement
AASd-108  SubmodelElementList children must match typeValueListElement
AASd-109  valueTypeListElement required and consistent when typeValueListElement is Property/Range
AASd-114  All SubmodelElementList children with semanticId must share the same one
AASd-117  Non-Identifiable Referables not under SubmodelElementList must have idShort
AASd-118  supplementalSemanticIds requires semanticId
AASd-119  TemplateQualifier requires element kind = "Template"
AASd-121  Reference first key type must be in GloballyIdentifiables
AASd-122  ExternalReference first key type must be GlobalReference
AASd-123  ModelReference first key type must be in AasIdentifiables
AASd-124  ExternalReference last key type must be in {GlobalReference, FragmentReference}
AASd-125  ModelReference following keys must be in FragmentKeys
AASd-126  FragmentReference must only appear as the last key in a ModelReference
AASd-127  FragmentReference key must be preceded by File or Blob
AASd-128  Key value after SubmodelElementList key must be a numeric string
AASd-129  SubmodelElement with TemplateQualifier must be under a Template Submodel
AASd-131  AssetInformation must have globalAssetId or at least one specificAssetId
AASd-133  SpecificAssetId/externalSubjectId must be an ExternalReference
AASd-134  Operation variable idShorts must be unique across input/output/inoutput

Skipped constraints (require external resolution or are informational)
-----------------------------------------------------------------------
AASd-006  Qualifier/value must equal the referenced coded value — needs external valueId resolution
AASd-007  Property/value must equal the referenced coded value — same reason
AASd-012  MultiLanguageProperty value/valueId language matching — same reason
AASd-020  Qualifier/value type consistency with valueType — requires XSD type parsing/validation
AASd-115  Informational default assumption for missing semanticId, not a structural violation
AASd-116  "globalAssetId" reserved key semantics — informational, not a structural violation
AASd-130  XML 1.0 character set on all string properties — prohibitively expensive to check in-graph

Note on AASd-107 and AASd-114
------------------------------
These constraints compare Reference nodes by identity (elementId). They work correctly only when
data was imported with AAS_NEO4J_MODEL_CONFIG, which enables Reference content-deduplication:
identical References share one Neo4j node, so same identity ⟺ same semantic content.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from neo4aas.core.base import BaseNeo4JClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# AASd-002: idShort must match this pattern (requires ≥2 characters per spec regex)
IDSHORT_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*[a-zA-Z0-9_]+$')

# AASd-125: all keys following the first key in a ModelReference must be FragmentKeys
FRAGMENT_KEYS: frozenset[str] = frozenset({
    "AnnotatedRelationshipElement",
    "BasicEventElement",
    "Blob",
    "Capability",
    "DataElement",
    "Entity",
    "EventElement",
    "File",
    "MultiLanguageProperty",
    "Operation",
    "Property",
    "Range",
    "ReferenceElement",
    "RelationshipElement",
    "SubmodelElement",
    "SubmodelElementCollection",
    "SubmodelElementList",
    "FragmentReference",
})

# Inline Cypher list literal built once from FRAGMENT_KEYS
_FRAGMENT_KEYS_CYPHER = "[" + ", ".join(f'"{k}"' for k in sorted(FRAGMENT_KEYS)) + "]"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ConstraintViolation:
    constraint_id: str
    description: str
    details: dict


@dataclass
class ConstraintReport:
    violations: list[ConstraintViolation] = field(default_factory=list)
    checked_constraints: list[str] = field(default_factory=list)

    def is_compliant(self) -> bool:
        return len(self.violations) == 0

    def summary(self) -> str:
        n_checked = len(self.checked_constraints)
        n_violations = len(self.violations)
        lines = [f"Checked {n_checked} constraint(s). {n_violations} violation(s) found."]
        if self.violations:
            from collections import defaultdict
            by_id: dict[str, list[ConstraintViolation]] = defaultdict(list)
            for v in self.violations:
                by_id[v.constraint_id].append(v)
            for cid in sorted(by_id):
                count = len(by_id[cid])
                lines.append(f"  {cid}: {count} violation(s) — {by_id[cid][0].description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

# Maps constraint ID → name of the private method that checks it
_CHECKER_METHODS: dict[str, str] = {
    "AASd-002": "_check_aasd002",
    "AASd-005": "_check_aasd005",
    "AASd-014": "_check_aasd014",
    "AASd-021": "_check_aasd021",
    "AASd-022": "_check_aasd022",
    "AASd-077": "_check_aasd077",
    "AASd-107": "_check_aasd107",
    "AASd-108": "_check_aasd108",
    "AASd-109": "_check_aasd109",
    "AASd-114": "_check_aasd114",
    "AASd-117": "_check_aasd117",
    "AASd-118": "_check_aasd118",
    "AASd-119": "_check_aasd119",
    "AASd-121": "_check_aasd121",
    "AASd-122": "_check_aasd122",
    "AASd-123": "_check_aasd123",
    "AASd-124": "_check_aasd124",
    "AASd-125": "_check_aasd125",
    "AASd-126": "_check_aasd126",
    "AASd-127": "_check_aasd127",
    "AASd-128": "_check_aasd128",
    "AASd-129": "_check_aasd129",
    "AASd-131": "_check_aasd131",
    "AASd-133": "_check_aasd133",
    "AASd-134": "_check_aasd134",
}


class AASConstraintChecker:
    """
    Checks AAS data stored in Neo4j against AAS specification constraints.

    Usage::

        from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
        from neo4aas.core.validation import AASConstraintChecker

        client = AASNeo4JClient("bolt://localhost:7687", "neo4j", "password", AAS_NEO4J_MODEL_CONFIG)
        report = AASConstraintChecker(client).check_all()
        print(report.summary())
    """

    def __init__(self, client: BaseNeo4JClient):
        self._client = client

    def check_all(self) -> ConstraintReport:
        """Run all implemented constraints and return a combined report."""
        return self.check(list(_CHECKER_METHODS.keys()))

    def check(self, constraint_ids: list[str]) -> ConstraintReport:
        """Run only the specified constraints.

        Raises ValueError for unknown constraint IDs.
        """
        unknown = set(constraint_ids) - set(_CHECKER_METHODS)
        if unknown:
            raise ValueError(f"Unknown constraint IDs: {sorted(unknown)}")
        report = ConstraintReport()
        for cid in constraint_ids:
            method = getattr(self, _CHECKER_METHODS[cid])
            report.violations.extend(method())
            report.checked_constraints.append(cid)
        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, query: str, params: dict | None = None) -> list:
        result = self._client.execute_clause(query, params=params)
        return result or []

    @staticmethod
    def _v(cid: str, description: str, **details) -> ConstraintViolation:
        return ConstraintViolation(constraint_id=cid, description=description, details=details)

    # ------------------------------------------------------------------
    # AASd-002 — idShort format
    # ------------------------------------------------------------------

    def _check_aasd002(self) -> list[ConstraintViolation]:
        """idShort of Referables must match ^[a-zA-Z][a-zA-Z0-9_-]*[a-zA-Z0-9_]+$."""
        query = """
        MATCH (n:Referable)
        WHERE n.idShort IS NOT NULL
        RETURN elementId(n) AS node_id, labels(n) AS node_labels, n.idShort AS id_short
        """
        violations = []
        for r in self._run(query):
            id_short = r["id_short"]
            if not IDSHORT_PATTERN.match(id_short):
                violations.append(self._v(
                    "AASd-002",
                    f"idShort {id_short!r} does not match the required pattern",
                    node_id=r["node_id"],
                    node_labels=list(r["node_labels"]),
                    field="idShort",
                    value=id_short,
                ))
        return violations

    # ------------------------------------------------------------------
    # AASd-005 — no revision without version
    # ------------------------------------------------------------------

    def _check_aasd005(self) -> list[ConstraintViolation]:
        """AdministrativeInformation/revision requires AdministrativeInformation/version."""
        query = """
        MATCH (parent:Identifiable)-[:administration]->(adm)
        WHERE adm.revision IS NOT NULL AND adm.version IS NULL
        RETURN elementId(parent) AS parent_node_id, elementId(adm) AS adm_node_id,
               parent.id AS identifiable_id, adm.revision AS revision_value
        """
        return [
            self._v(
                "AASd-005",
                "revision is set but version is missing in AdministrativeInformation",
                parent_node_id=r["parent_node_id"],
                adm_node_id=r["adm_node_id"],
                identifiable_id=r["identifiable_id"],
                revision_value=r["revision_value"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-014 — SelfManagedEntity must have globalAssetId or specificAssetId
    # ------------------------------------------------------------------

    def _check_aasd014(self) -> list[ConstraintViolation]:
        """Entity with entityType=SelfManagedEntity must have globalAssetId or specificAssetId."""
        query = """
        MATCH (e:Entity {entityType: "SelfManagedEntity"})
        WHERE e.globalAssetId IS NULL
          AND NOT EXISTS { MATCH (e)-[:specificAssetId]->() }
        RETURN elementId(e) AS node_id, labels(e) AS node_labels, e.idShort AS id_short
        """
        return [
            self._v(
                "AASd-014",
                "SelfManagedEntity has neither globalAssetId nor specificAssetId",
                node_id=r["node_id"],
                node_labels=list(r["node_labels"]),
                id_short=r["id_short"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-021 — Qualifier type uniqueness per Qualifiable
    # ------------------------------------------------------------------

    def _check_aasd021(self) -> list[ConstraintViolation]:
        """A Qualifiable must not have two Qualifiers with the same type."""
        query = """
        MATCH (parent:Qualifiable)-[:qualifiers]->(q:Qualifier)
        WITH parent, q.type AS qtype, collect(elementId(q)) AS qualifier_ids
        WHERE size(qualifier_ids) > 1
        RETURN elementId(parent) AS parent_node_id, labels(parent) AS parent_labels,
               parent.idShort AS id_short, qtype AS duplicate_qualifier_type, qualifier_ids
        """
        return [
            self._v(
                "AASd-021",
                f"Duplicate qualifier type {r['duplicate_qualifier_type']!r}",
                parent_node_id=r["parent_node_id"],
                parent_labels=list(r["parent_labels"]),
                id_short=r["id_short"],
                duplicate_qualifier_type=r["duplicate_qualifier_type"],
                qualifier_ids=list(r["qualifier_ids"]),
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-022 — idShort uniqueness within parent namespace
    # ------------------------------------------------------------------

    def _check_aasd022(self) -> list[ConstraintViolation]:
        """Non-identifiable Referables within the same namespace must have unique idShorts.

        Note: uses any outgoing relationship from a Referable parent to a Referable child
        because [:child] relationships are not created for list children during batch import.
        Virtual relationships (child, references) are excluded to avoid double-counting.

        Two-pass approach to avoid materializing large collections across the full graph:
        pass 1 uses count() (streaming) to find violating (parent, idShort) pairs;
        pass 2 fetches child_ids only for those violations using targeted parameterised queries.
        """
        find_query = """
        MATCH (parent:Referable)-[r]->(child:Referable)
        WHERE NOT parent:SubmodelElementList
          AND NOT type(r) IN ['child', 'references']
          AND child.idShort IS NOT NULL
        WITH parent, child.idShort AS id_short, count(DISTINCT child) AS cnt
        WHERE cnt > 1
        RETURN elementId(parent) AS parent_node_id, labels(parent) AS parent_labels, id_short
        """
        violating = self._run(find_query)
        if not violating:
            return []

        detail_query = """
        MATCH (parent:Referable)-[r]->(child:Referable)
        WHERE elementId(parent) = $parent_id
          AND NOT type(r) IN ['child', 'references']
          AND child.idShort = $id_short
        RETURN collect(DISTINCT elementId(child)) AS child_ids
        """
        violations = []
        for row in violating:
            detail = self._run(detail_query, {"parent_id": row["parent_node_id"], "id_short": row["id_short"]})
            child_ids = detail[0]["child_ids"] if detail else []
            violations.append(self._v(
                "AASd-022",
                f"Duplicate idShort {row['id_short']!r} within parent namespace",
                parent_node_id=row["parent_node_id"],
                parent_labels=list(row["parent_labels"]),
                duplicate_id_short=row["id_short"],
                child_ids=list(child_ids),
            ))
        return violations

    # ------------------------------------------------------------------
    # AASd-077 — Extension name uniqueness
    # ------------------------------------------------------------------

    def _check_aasd077(self) -> list[ConstraintViolation]:
        """Extension names within the same HasExtensions element must be unique."""
        query = """
        MATCH (parent:Referable)-[:extensions]->(ext)
        WITH parent, ext.name AS ext_name, collect(elementId(ext)) AS ext_ids
        WHERE size(ext_ids) > 1
        RETURN elementId(parent) AS parent_node_id, labels(parent) AS parent_labels,
               parent.idShort AS id_short, ext_name AS duplicate_extension_name, ext_ids
        """
        return [
            self._v(
                "AASd-077",
                f"Duplicate extension name {r['duplicate_extension_name']!r}",
                parent_node_id=r["parent_node_id"],
                parent_labels=list(r["parent_labels"]),
                id_short=r["id_short"],
                duplicate_extension_name=r["duplicate_extension_name"],
                ext_ids=list(r["ext_ids"]),
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-107 — SubmodelElementList child semanticId matches semanticIdListElement
    # ------------------------------------------------------------------

    def _check_aasd107(self) -> list[ConstraintViolation]:
        """First-level children of SubmodelElementList with a semanticId must match semanticIdListElement."""
        query = """
        MATCH (sml:SubmodelElementList)-[:semanticIdListElement]->(expected_sem:Reference)
        MATCH (sml)-[:value]->(child:SubmodelElement)-[:semanticId]->(actual_sem:Reference)
        WHERE elementId(actual_sem) <> elementId(expected_sem)
        RETURN elementId(sml) AS list_node_id, sml.idShort AS list_id_short,
               elementId(child) AS child_node_id, labels(child) AS child_labels,
               elementId(expected_sem) AS expected_sem_id, elementId(actual_sem) AS actual_sem_id
        """
        return [
            self._v(
                "AASd-107",
                "Child semanticId differs from SubmodelElementList/semanticIdListElement",
                list_node_id=r["list_node_id"],
                list_id_short=r["list_id_short"],
                child_node_id=r["child_node_id"],
                child_labels=list(r["child_labels"]),
                expected_sem_id=r["expected_sem_id"],
                actual_sem_id=r["actual_sem_id"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-108 — SubmodelElementList children match typeValueListElement
    # ------------------------------------------------------------------

    def _check_aasd108(self) -> list[ConstraintViolation]:
        """All first-level children of a SubmodelElementList must have the type specified by typeValueListElement."""
        query = """
        MATCH (sml:SubmodelElementList)-[:value]->(child)
        WHERE sml.typeValueListElement IS NOT NULL
          AND NOT sml.typeValueListElement IN labels(child)
        RETURN elementId(sml) AS list_node_id, sml.idShort AS list_id_short,
               elementId(child) AS child_node_id, labels(child) AS child_labels,
               sml.typeValueListElement AS expected_type
        """
        return [
            self._v(
                "AASd-108",
                f"Child type does not match SubmodelElementList/typeValueListElement {r['expected_type']!r}",
                list_node_id=r["list_node_id"],
                list_id_short=r["list_id_short"],
                child_node_id=r["child_node_id"],
                child_labels=list(r["child_labels"]),
                expected_type=r["expected_type"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-109 — valueTypeListElement required and consistent
    # ------------------------------------------------------------------

    def _check_aasd109(self) -> list[ConstraintViolation]:
        """If typeValueListElement is Property or Range, valueTypeListElement must be set and consistent."""
        violations = []

        # Part A: valueTypeListElement missing
        query_a = """
        MATCH (sml:SubmodelElementList)
        WHERE sml.typeValueListElement IN ["Property", "Range"]
          AND sml.valueTypeListElement IS NULL
        RETURN elementId(sml) AS list_node_id, sml.idShort AS list_id_short,
               sml.typeValueListElement AS type_value_list_element
        """
        for r in self._run(query_a):
            violations.append(self._v(
                "AASd-109",
                "SubmodelElementList with Property/Range typeValueListElement is missing valueTypeListElement",
                list_node_id=r["list_node_id"],
                list_id_short=r["list_id_short"],
                type_value_list_element=r["type_value_list_element"],
                issue="missing_valueTypeListElement",
            ))

        # Part B: children with wrong valueType
        query_b = """
        MATCH (sml:SubmodelElementList)-[:value]->(child)
        WHERE sml.typeValueListElement IN ["Property", "Range"]
          AND sml.valueTypeListElement IS NOT NULL
          AND child.valueType <> sml.valueTypeListElement
        RETURN elementId(sml) AS list_node_id, sml.idShort AS list_id_short,
               elementId(child) AS child_node_id, labels(child) AS child_labels,
               sml.valueTypeListElement AS expected_value_type,
               child.valueType AS actual_value_type
        """
        for r in self._run(query_b):
            violations.append(self._v(
                "AASd-109",
                f"Child valueType {r['actual_value_type']!r} does not match valueTypeListElement {r['expected_value_type']!r}",
                list_node_id=r["list_node_id"],
                list_id_short=r["list_id_short"],
                child_node_id=r["child_node_id"],
                child_labels=list(r["child_labels"]),
                expected_value_type=r["expected_value_type"],
                actual_value_type=r["actual_value_type"],
                issue="wrong_child_value_type",
            ))

        return violations

    # ------------------------------------------------------------------
    # AASd-114 — All children semanticIds in SubmodelElementList must be identical
    # ------------------------------------------------------------------

    def _check_aasd114(self) -> list[ConstraintViolation]:
        """If multiple SubmodelElementList children have a semanticId, they must all be identical."""
        query = """
        MATCH (sml:SubmodelElementList)-[:value]->(child)-[:semanticId]->(sem:Reference)
        WITH sml, collect(DISTINCT elementId(sem)) AS distinct_sem_ids
        WHERE size(distinct_sem_ids) > 1
        RETURN elementId(sml) AS list_node_id, sml.idShort AS list_id_short, distinct_sem_ids
        """
        return [
            self._v(
                "AASd-114",
                "SubmodelElementList children have differing semanticIds",
                list_node_id=r["list_node_id"],
                list_id_short=r["list_id_short"],
                distinct_sem_ids=list(r["distinct_sem_ids"]),
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-117 — Non-Identifiable Referables not under SubmodelElementList need idShort
    # ------------------------------------------------------------------

    def _check_aasd117(self) -> list[ConstraintViolation]:
        """Non-Identifiable Referables that are not direct children of SubmodelElementList must have idShort.

        Note: uses any incoming relationship to a Referable because [:child] is not created
        for list children during batch import. Virtual relationships are excluded.
        """
        query = """
        MATCH (parent)-[r]->(n:Referable)
        WHERE NOT n:Identifiable
          AND NOT parent:SubmodelElementList
          AND NOT type(r) IN ['child', 'references']
          AND n.idShort IS NULL
        RETURN DISTINCT elementId(n) AS node_id, labels(n) AS node_labels,
               elementId(parent) AS parent_node_id, labels(parent) AS parent_labels
        """
        return [
            self._v(
                "AASd-117",
                "Non-Identifiable Referable missing idShort",
                node_id=r["node_id"],
                node_labels=list(r["node_labels"]),
                parent_node_id=r["parent_node_id"],
                parent_labels=list(r["parent_labels"]),
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-118 — supplementalSemanticIds requires semanticId
    # ------------------------------------------------------------------

    def _check_aasd118(self) -> list[ConstraintViolation]:
        """If supplementalSemanticIds is defined, semanticId must also be defined."""
        query = """
        MATCH (n)-[:supplementalSemanticIds]->()
        WHERE NOT EXISTS { MATCH (n)-[:semanticId]->() }
        RETURN DISTINCT elementId(n) AS node_id, labels(n) AS node_labels, n.idShort AS id_short
        """
        return [
            self._v(
                "AASd-118",
                "supplementalSemanticIds present but semanticId is missing",
                node_id=r["node_id"],
                node_labels=list(r["node_labels"]),
                id_short=r["id_short"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-119 — TemplateQualifier requires element kind = "Template"
    # ------------------------------------------------------------------

    def _check_aasd119(self) -> list[ConstraintViolation]:
        """If a Qualifier with kind=TemplateQualifier is present and the element has HasKind, kind must be Template."""
        query = """
        MATCH (parent:Qualifiable)-[:qualifiers]->(q:Qualifier {kind: "TemplateQualifier"})
        WHERE parent.kind IS NOT NULL AND parent.kind <> "Template"
        RETURN DISTINCT elementId(parent) AS node_id, labels(parent) AS node_labels,
               parent.idShort AS id_short, parent.kind AS actual_kind
        """
        return [
            self._v(
                "AASd-119",
                f"TemplateQualifier used on element with kind={r['actual_kind']!r} (expected Template)",
                node_id=r["node_id"],
                node_labels=list(r["node_labels"]),
                id_short=r["id_short"],
                actual_kind=r["actual_kind"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-121 — Reference first key in GloballyIdentifiables
    # ------------------------------------------------------------------

    def _check_aasd121(self) -> list[ConstraintViolation]:
        """First key type of any Reference must be in GloballyIdentifiables."""
        query = """
        MATCH (ref:Reference)
        WHERE size(ref.keys_type) > 0
          AND NOT ref.keys_type[0] IN [
            "AssetAdministrationShell", "ConceptDescription", "Submodel", "GlobalReference"
          ]
        RETURN elementId(ref) AS node_id, ref.keys_type[0] AS first_key_type,
               ref.keys_value[0] AS first_key_value, ref.type AS ref_type
        """
        return [
            self._v(
                "AASd-121",
                f"Reference first key type {r['first_key_type']!r} is not in GloballyIdentifiables",
                node_id=r["node_id"],
                first_key_type=r["first_key_type"],
                first_key_value=r["first_key_value"],
                ref_type=r["ref_type"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-122 — ExternalReference first key must be GlobalReference
    # ------------------------------------------------------------------

    def _check_aasd122(self) -> list[ConstraintViolation]:
        """First key type of ExternalReferences must be GlobalReference."""
        query = """
        MATCH (ref:Reference {type: "ExternalReference"})
        WHERE size(ref.keys_type) > 0
          AND ref.keys_type[0] <> "GlobalReference"
        RETURN elementId(ref) AS node_id, ref.keys_type[0] AS first_key_type,
               ref.keys_value[0] AS first_key_value
        """
        return [
            self._v(
                "AASd-122",
                f"ExternalReference first key type is {r['first_key_type']!r} (must be GlobalReference)",
                node_id=r["node_id"],
                first_key_type=r["first_key_type"],
                first_key_value=r["first_key_value"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-123 — ModelReference first key in AasIdentifiables
    # ------------------------------------------------------------------

    def _check_aasd123(self) -> list[ConstraintViolation]:
        """First key type of ModelReferences must be in AasIdentifiables."""
        query = """
        MATCH (ref:Reference {type: "ModelReference"})
        WHERE size(ref.keys_type) > 0
          AND NOT ref.keys_type[0] IN [
            "AssetAdministrationShell", "ConceptDescription", "Submodel"
          ]
        RETURN elementId(ref) AS node_id, ref.keys_type[0] AS first_key_type,
               ref.keys_value[0] AS first_key_value
        """
        return [
            self._v(
                "AASd-123",
                f"ModelReference first key type {r['first_key_type']!r} is not in AasIdentifiables",
                node_id=r["node_id"],
                first_key_type=r["first_key_type"],
                first_key_value=r["first_key_value"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-124 — ExternalReference last key in {GlobalReference, FragmentReference}
    # ------------------------------------------------------------------

    def _check_aasd124(self) -> list[ConstraintViolation]:
        """Last key type of ExternalReferences must be GlobalReference or FragmentReference."""
        query = """
        MATCH (ref:Reference {type: "ExternalReference"})
        WHERE size(ref.keys_type) > 0
          AND NOT ref.keys_type[-1] IN ["GlobalReference", "FragmentReference"]
        RETURN elementId(ref) AS node_id, ref.keys_type[-1] AS last_key_type,
               ref.keys_value[-1] AS last_key_value
        """
        return [
            self._v(
                "AASd-124",
                f"ExternalReference last key type {r['last_key_type']!r} is not in GenericGloballyIdentifiables or GenericFragmentKeys",
                node_id=r["node_id"],
                last_key_type=r["last_key_type"],
                last_key_value=r["last_key_value"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-125 — ModelReference following keys in FragmentKeys
    # ------------------------------------------------------------------

    def _check_aasd125(self) -> list[ConstraintViolation]:
        """In a ModelReference with >1 key, all keys after the first must be in FragmentKeys."""
        query = f"""
        MATCH (ref:Reference {{type: "ModelReference"}})
        WHERE size(ref.keys_type) > 1
        WITH ref, [i IN range(1, size(ref.keys_type)-1)
                   WHERE NOT ref.keys_type[i] IN {_FRAGMENT_KEYS_CYPHER}
                   | {{idx: i, key_type: ref.keys_type[i], key_value: ref.keys_value[i]}}
                  ] AS bad_keys
        WHERE size(bad_keys) > 0
        RETURN elementId(ref) AS node_id, bad_keys
        """
        return [
            self._v(
                "AASd-125",
                "ModelReference contains keys outside FragmentKeys after the first key",
                node_id=r["node_id"],
                bad_keys=[dict(k) for k in r["bad_keys"]],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-126 — FragmentReference must only be the last key
    # ------------------------------------------------------------------

    def _check_aasd126(self) -> list[ConstraintViolation]:
        """In a ModelReference with >1 key, FragmentReference may only appear as the last key."""
        query = """
        MATCH (ref:Reference {type: "ModelReference"})
        WHERE size(ref.keys_type) > 1
        WITH ref, [i IN range(0, size(ref.keys_type)-2)
                   WHERE ref.keys_type[i] = "FragmentReference"
                   | {idx: i, key_value: ref.keys_value[i]}
                  ] AS bad_positions
        WHERE size(bad_positions) > 0
        RETURN elementId(ref) AS node_id, bad_positions
        """
        return [
            self._v(
                "AASd-126",
                "FragmentReference appears at a non-final position in ModelReference",
                node_id=r["node_id"],
                bad_positions=[dict(p) for p in r["bad_positions"]],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-127 — FragmentReference must be preceded by File or Blob
    # ------------------------------------------------------------------

    def _check_aasd127(self) -> list[ConstraintViolation]:
        """In a ModelReference, a FragmentReference key must be immediately preceded by a File or Blob key."""
        query = """
        MATCH (ref:Reference {type: "ModelReference"})
        WHERE size(ref.keys_type) > 1
          AND ref.keys_type[-1] = "FragmentReference"
          AND NOT ref.keys_type[-2] IN ["File", "Blob"]
        RETURN elementId(ref) AS node_id, ref.keys_type[-2] AS preceding_key_type,
               ref.keys_value[-1] AS fragment_value
        """
        return [
            self._v(
                "AASd-127",
                f"FragmentReference preceded by {r['preceding_key_type']!r} instead of File or Blob",
                node_id=r["node_id"],
                preceding_key_type=r["preceding_key_type"],
                fragment_value=r["fragment_value"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-128 — Key after SubmodelElementList must be numeric
    # ------------------------------------------------------------------

    def _check_aasd128(self) -> list[ConstraintViolation]:
        """In a ModelReference, the key value following a SubmodelElementList key must be an integer string."""
        query = r"""
        MATCH (ref:Reference {type: "ModelReference"})
        WHERE size(ref.keys_type) > 1
        WITH ref, [i IN range(0, size(ref.keys_type)-2)
                   WHERE ref.keys_type[i] = "SubmodelElementList"
                     AND NOT ref.keys_value[i+1] =~ '^[0-9]+$'
                   | {idx: i+1, key_type: ref.keys_type[i+1], key_value: ref.keys_value[i+1]}
                  ] AS bad_entries
        WHERE size(bad_entries) > 0
        RETURN elementId(ref) AS node_id, bad_entries
        """
        return [
            self._v(
                "AASd-128",
                "Key value after SubmodelElementList is not an integer",
                node_id=r["node_id"],
                bad_entries=[dict(e) for e in r["bad_entries"]],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-129 — SubmodelElement with TemplateQualifier must be under Template Submodel
    # ------------------------------------------------------------------

    def _check_aasd129(self) -> list[ConstraintViolation]:
        """SubmodelElements with a TemplateQualifier must be part of a Template Submodel.

        Note: uses [:submodelElements|value|statements*1..] path — may be slow on very large graphs.
        """
        query = """
        MATCH (sme:SubmodelElement)-[:qualifiers]->(q:Qualifier {kind: "TemplateQualifier"})
        WHERE NOT EXISTS {
            MATCH (sm:Submodel {kind: "Template"})-[:submodelElements|value|statements*1..]->(sme)
        }
        RETURN DISTINCT elementId(sme) AS node_id, labels(sme) AS node_labels,
               sme.idShort AS id_short
        """
        return [
            self._v(
                "AASd-129",
                "SubmodelElement has TemplateQualifier but is not under a Template Submodel",
                node_id=r["node_id"],
                node_labels=list(r["node_labels"]),
                id_short=r["id_short"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-131 — AssetInformation must have globalAssetId or specificAssetIds
    # ------------------------------------------------------------------

    def _check_aasd131(self) -> list[ConstraintViolation]:
        """AssetInformation must define either globalAssetId or at least one specificAssetId."""
        query = """
        MATCH (ai:AssetInformation)
        WHERE ai.globalAssetId IS NULL
          AND NOT EXISTS { MATCH (ai)-[:specificAssetIds]->() }
        RETURN elementId(ai) AS node_id
        """
        return [
            self._v(
                "AASd-131",
                "AssetInformation has neither globalAssetId nor specificAssetIds",
                node_id=r["node_id"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-133 — SpecificAssetId/externalSubjectId must be ExternalReference
    # ------------------------------------------------------------------

    def _check_aasd133(self) -> list[ConstraintViolation]:
        """SpecificAssetId/externalSubjectId must be a global reference (ExternalReference)."""
        query = """
        MATCH (sai)-[:externalSubjectId]->(ref:Reference)
        WHERE ref.type <> "ExternalReference"
        RETURN elementId(sai) AS node_id, elementId(ref) AS ref_node_id,
               ref.type AS actual_ref_type
        """
        return [
            self._v(
                "AASd-133",
                f"externalSubjectId is {r['actual_ref_type']!r} but must be ExternalReference",
                node_id=r["node_id"],
                ref_node_id=r["ref_node_id"],
                actual_ref_type=r["actual_ref_type"],
            )
            for r in self._run(query)
        ]

    # ------------------------------------------------------------------
    # AASd-134 — Operation variable idShorts unique across all three categories
    # ------------------------------------------------------------------

    def _check_aasd134(self) -> list[ConstraintViolation]:
        """idShorts of inputVariable/outputVariable/inoutputVariable values must be unique within the Operation."""
        query = """
        MATCH (op:Operation)-[:inputVariables|outputVariables|inoutputVariables]->(ov)-[:value]->(sme:SubmodelElement)
        WITH op, sme.idShort AS id_short, collect(elementId(sme)) AS sme_ids
        WHERE size(sme_ids) > 1 AND id_short IS NOT NULL
        RETURN elementId(op) AS op_node_id, op.idShort AS op_id_short,
               id_short AS duplicate_id_short, sme_ids
        """
        return [
            self._v(
                "AASd-134",
                f"Operation variable idShort {r['duplicate_id_short']!r} is not unique",
                op_node_id=r["op_node_id"],
                op_id_short=r["op_id_short"],
                duplicate_id_short=r["duplicate_id_short"],
                sme_ids=list(r["sme_ids"]),
            )
            for r in self._run(query)
        ]
