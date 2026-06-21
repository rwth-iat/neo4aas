"""Shared, transport-agnostic read-only AAS tools over an ``AASNeo4JClient``.

These functions are the single source of truth for the capabilities exposed both by
the MCP server (`aas_mapping/mcp_server/server.py`) and the chatbot agent
(`aas_mapping/chatbot`). Each takes a client and returns a plain JSON-able dict and
raises ``ValueError`` for user-facing errors. All are read-only.
"""

import re
from typing import Any, Dict, List, Optional

from aas_mapping.aas_neo4j_adapter.validation import AASConstraintChecker
from aas_mapping.aas_neo4j_adapter.abstract import build_abstract_submodel

# Reject anything that could mutate the graph (defense-in-depth on top of the read tx).
_WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b"
    r"|apoc\.\w*\.(create|merge|delete|set)",
    re.IGNORECASE,
)

_LIST_TYPES = (
    "MATCH (sm:Submodel) "
    "OPTIONAL MATCH (sm)-[:semanticId]->(sem:Reference) "
    "RETURN sm.idShort AS idShort, sem.keys_value[0] AS semanticId, COUNT(sm) AS count "
    "ORDER BY count DESC, idShort"
)
_LIST_TYPES_BY_SEM = (
    "MATCH (sm:Submodel) "
    "OPTIONAL MATCH (sm)-[:semanticId]->(sem:Reference) "
    "RETURN sem.keys_value[0] AS semanticId, COUNT(sm) AS count "
    "ORDER BY count DESC, semanticId"
)


def count_stats(client) -> Dict[str, int]:
    """Counts of AssetAdministrationShells, Submodels and ConceptDescriptions."""
    return client.count_identifiables_by_type()


def get_identifiable(client, identifier: str) -> Dict[str, Any]:
    """Fetch a top-level Identifiable by global id, as AAS JSON."""
    try:
        return client.get_identifiable(identifier)
    except KeyError:
        raise ValueError(f"No Identifiable found with id '{identifier}'.")


def get_referable(client, parent_id: str, id_short_path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch a Referable (optionally nested via an idShort path) as AAS JSON."""
    try:
        return client.get_referable(parent_id, id_short_path)
    except KeyError:
        target = parent_id if not id_short_path else f"{parent_id} -> {id_short_path}"
        raise ValueError(f"No Referable found at '{target}'.")


def list_submodel_types(client) -> Dict[str, Any]:
    """Distinct Submodel types (idShort + semanticId) with an instance count each."""
    rows = client.execute_clause(_LIST_TYPES) or []
    types = [{"idShort": r["idShort"], "semanticId": r["semanticId"], "count": r["count"]}
             for r in rows]
    return {"total_types": len(types), "types": types}


def list_submodel_types_by_semantic_id(client) -> Dict[str, Any]:
    """Distinct Submodel semanticIds (idShort ignored) with an instance count each."""
    rows = client.execute_clause(_LIST_TYPES_BY_SEM) or []
    types = [{"semanticId": r["semanticId"], "count": r["count"]} for r in rows]
    return {"total_types": len(types), "types": types}


def abstract_submodel(client, submodel_type: str, output_format: str = "json") -> Dict[str, Any]:
    """Build a Template-kind structural union of all Submodels of a given type.

    Matches semanticId first (the real type discriminator), then falls back to idShort.
    """
    instances = client.get_submodels_by_type(submodel_type, by_semantic_id=True)
    if not instances:
        instances = client.get_submodels_by_type(submodel_type, by_semantic_id=False)
    if not instances:
        raise ValueError(
            f"No Submodels found for type '{submodel_type}'. "
            "Use list_submodel_types to browse available types."
        )
    abstract = build_abstract_submodel(instances)
    if output_format == "yaml":
        import yaml
        return {"instance_count": len(instances), "submodel_type": submodel_type,
                "yaml": yaml.dump(abstract, allow_unicode=True, sort_keys=False)}
    return {"instance_count": len(instances), "submodel_type": submodel_type,
            "abstract_submodel": abstract}


def validate_constraints(client, constraint_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Validate AAS data against the AAS spec constraints; return a compliance report."""
    checker = AASConstraintChecker(client)
    report = checker.check(constraint_ids) if constraint_ids else checker.check_all()
    return {
        "compliant": report.is_compliant(),
        "summary": report.summary(),
        "checked_constraints": report.checked_constraints,
        "violations": [
            {"constraint_id": v.constraint_id, "description": v.description, "details": v.details}
            for v in report.violations
        ],
    }


def cypher_read(client, cypher: str) -> Dict[str, Any]:
    """Run a READ-ONLY Cypher query against the Neo4j backend and return rows.

    Enforced two ways: a write-keyword denylist, and a Neo4j READ transaction
    (``execute_read``) that rejects writes server-side.
    """
    cypher = (cypher or "").strip()
    if not cypher:
        raise ValueError("Empty cypher.")
    if _WRITE.search(cypher):
        raise ValueError("Only read-only Cypher is permitted (no writes).")
    if client.driver is None:
        raise ValueError("No Neo4j driver configured.")
    with client.driver.session(default_access_mode="READ") as session:
        rows = session.execute_read(lambda tx: [r.data() for r in tx.run(cypher)])
    return {"count": len(rows), "rows": rows}
