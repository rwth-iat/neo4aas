"""Agent tools (skills) the orchestrator can call.

Each tool exposes a ``name``, a ``description`` and an ``args`` hint (both fed into
the orchestrator's system prompt), and a ``run(args) -> dict`` that returns an
observation. Tools are read-only.
"""

import json
import re

import requests

from config import REPOSITORY_URL, MODEL_LARGE, neo4j_enabled, get_aas_client, log
from llm import llm_call, extract_json
from system_prompt import SYSTEM_PROMPT

# Keep observations small so they fit the orchestrator context.
_MAX_ROWS = 50


def _truncate(rows: list, limit: int = _MAX_ROWS) -> tuple[list, int]:
    return rows[:limit], len(rows)


class Tool:
    name: str = "tool"
    description: str = ""
    args: str = ""

    def run(self, args: dict) -> dict:
        raise NotImplementedError


def _detect_target(aasql: dict) -> str:
    text = json.dumps(aasql)
    return "shells" if ('"$aas' in text or "'$aas" in text) else "submodels"


class AasqlQueryTool(Tool):
    name = "aasql_query"
    description = (
        "Search the AAS Repository with the AAS Query Language. Give a natural-language "
        "request; an AASQL query is generated and executed. Use for content searches over "
        "shells/submodels (by idShort, property values, semanticId, etc.)."
    )
    args = '{"question": "<the user\'s question, verbatim>"}'

    def _post(self, aasql: dict, target: str) -> dict:
        url = f"{REPOSITORY_URL}/query/{target}"
        try:
            resp = requests.post(url, json=aasql, timeout=30)
        except Exception as exc:
            return {"error": f"Repository unreachable: {exc}"}
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:400]}"}
        return {"results": resp.json().get("result", [])}

    def run(self, args: dict) -> dict:
        question = args.get("question", "")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        content, _ = llm_call(MODEL_LARGE, messages)
        aasql = extract_json(content)
        # The valid endpoint is determined by the generated query's root ($aas → shells,
        # else submodels), NOT by any caller hint — a mismatched endpoint returns nothing.
        target = _detect_target(aasql)
        out = self._post(aasql, target)
        # Self-correct: a cross-root query can be valid on the other endpoint, so if the
        # primary returned 0 (and didn't error), retry the opposite target once.
        if "error" not in out and not out["results"]:
            other = "submodels" if target == "shells" else "shells"
            retry = self._post(aasql, other)
            if "error" not in retry and retry["results"]:
                out, target = retry, other
        if "error" in out:
            return {"aasql": aasql, "target": target, "error": out["error"]}
        shown, total = _truncate(out["results"])
        return {"aasql": aasql, "target": target, "count": total, "results": shown}


class RepoReadTool(Tool):
    name = "repo_read"
    description = (
        "Issue a read (GET) request to the AAS Repository REST API. Use to list or fetch "
        "shells / submodels / concept-descriptions, or a specific one by id, or its "
        "submodel-elements. Paths: /shells, /submodels, /concept-descriptions, "
        "/shells/{base64id}, /submodels/{base64id}, /submodels/{base64id}/submodel-elements."
    )
    args = '{"path": "/shells", "params": {"limit": 20}}'

    _ALLOWED = re.compile(r"^/(shells|submodels|concept-descriptions)(/[^?#]*)?$")

    def run(self, args: dict) -> dict:
        path = (args.get("path") or "").strip()
        params = args.get("params") or {}
        if not path.startswith("/"):
            path = "/" + path
        if ".." in path or not self._ALLOWED.match(path):
            return {"error": f"Path not allowed (read-only whitelist): {path}"}
        url = f"{REPOSITORY_URL}{path}"
        try:
            resp = requests.get(url, params=params, timeout=30)
        except Exception as exc:
            return {"error": f"Repository unreachable: {exc}"}
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:400]}"}
        try:
            data = resp.json()
        except ValueError:
            return {"text": resp.text[:2000]}
        # AAS list endpoints wrap items under "result".
        if isinstance(data, dict) and isinstance(data.get("result"), list):
            shown, total = _truncate(data["result"])
            return {"path": path, "count": total, "results": shown}
        return {"path": path, "data": data}


class AgentTool(Tool):
    """Wrap a shared `agent_tools` function (operating on the AASNeo4JClient) as a Tool.

    These mirror the MCP server's tools — single source of truth in
    `aas_mapping.aas_neo4j_adapter.agent_tools`. Available only when a Neo4j backend
    is configured.
    """

    def __init__(self, name: str, description: str, args: str, fn, arg_names: list):
        self.name = name
        self.description = description
        self.args = args
        self._fn = fn
        self._arg_names = arg_names

    def run(self, args: dict) -> dict:
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured (NEO4J_URI unset)."}
        kwargs = {k: args[k] for k in self._arg_names if k in args and args[k] is not None}
        try:
            return self._fn(client, **kwargs)
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"{self.name} failed: {str(exc)[:400]}"}


def _agent_tools() -> list:
    """Shared neo4aas tools (same surface as the MCP server), Neo4j-backed."""
    from aas_mapping.aas_neo4j_adapter import agent_tools as at
    return [
        AgentTool("cypher_read",
                  "Run a READ-ONLY Cypher query against the Neo4j (neo4aas) backend for "
                  "aggregate/graph questions: counts, distinct semanticIds, traversals, "
                  "ECLASS/IRDI discovery. Labels: Identifiable, AssetAdministrationShell, "
                  "Submodel, ConceptDescription, Referable, SubmodelElement, Property, "
                  "MultiLanguageProperty, Reference. Rels: :submodels, :submodelElements, "
                  ":value, :semanticId, :references. Reference nodes carry keys_value[], "
                  "target_id, target_id_base (IRDI without version). RETURN explicit columns.",
                  '{"cypher": "MATCH (s:Submodel) RETURN count(s) AS n"}',
                  at.cypher_read, ["cypher"]),
        AgentTool("count_stats",
                  "Counts of AssetAdministrationShells, Submodels and ConceptDescriptions.",
                  "{}", at.count_stats, []),
        AgentTool("repo_overview",
                  "Factual snapshot of the repository for grounding: total counts, the "
                  "submodel types, the exact list of manufacturers present, and all asset "
                  "(AAS) idShort tags. Call this for open-ended 'what is in the repo' "
                  "questions, or to learn the real spellings before searching.",
                  "{}", at.repo_overview, []),
        AgentTool("list_submodel_types",
                  "Distinct Submodel types (idShort + semanticId) with an instance count each.",
                  "{}", at.list_submodel_types, []),
        AgentTool("list_submodel_types_by_semantic_id",
                  "Distinct Submodel semanticIds (idShort ignored) with an instance count each.",
                  "{}", at.list_submodel_types_by_semantic_id, []),
        AgentTool("get_identifiable",
                  "Fetch a top-level Identifiable (AAS/Submodel/ConceptDescription) by global id.",
                  '{"identifier": "<global id>"}', at.get_identifiable, ["identifier"]),
        AgentTool("get_referable",
                  "Fetch a Referable by parent id + optional idShort path "
                  "(e.g. 'Coll.List[0].Prop').",
                  '{"parent_id": "<id>", "id_short_path": "A.B"}',
                  at.get_referable, ["parent_id", "id_short_path"]),
        AgentTool("abstract_submodel",
                  "Build a Template-kind structural union of all Submodels of a given type "
                  "(matched by semanticId, then idShort).",
                  '{"submodel_type": "<semanticId or idShort>", "output_format": "json"}',
                  at.abstract_submodel, ["submodel_type", "output_format"]),
        AgentTool("validate_constraints",
                  "Validate the AAS data against the AAS spec constraints; returns a report.",
                  '{"constraint_ids": ["AASd-002"]}', at.validate_constraints, ["constraint_ids"]),
    ]


_repo_context_cache: str | None = None


def repo_context_text() -> str:
    """A compact, factual repository profile injected into the orchestrator prompt so it
    grounds tool calls in real values. Built once from `repo_overview` and cached;
    empty string when no Neo4j backend is configured."""
    global _repo_context_cache
    if _repo_context_cache is not None:
        return _repo_context_cache
    client = get_aas_client()
    if client is None:
        _repo_context_cache = ""
        return _repo_context_cache
    try:
        from aas_mapping.aas_neo4j_adapter import agent_tools as at
        ov = at.repo_overview(client)
    except Exception as exc:
        log.warning("repo_overview failed, no repo context: %s", exc)
        _repo_context_cache = ""
        return _repo_context_cache
    c = ov["counts"]
    types = ", ".join(f"{t['idShort']} ({t['count']})" for t in ov["submodel_types"])
    mans = ", ".join(ov["manufacturers"][:40])
    assets = ", ".join(ov["asset_names"][:80])
    _repo_context_cache = (
        "REPOSITORY FACTS (live snapshot — use these exact spellings; do not invent):\n"
        f"- Totals: {c.get('assetAdministrationShells')} AAS, {c.get('submodels')} submodels, "
        f"{c.get('conceptDescriptions')} concept descriptions.\n"
        f"- Submodel types (instances): {types}.\n"
        f"- Manufacturers present: {mans}.\n"
        f"- Asset (AAS) idShort tags: {assets}.\n"
        "Manufacturer/value spellings often differ from how a user phrases them "
        "(e.g. 'Endress+Hauser', 'Samson AG', 'Krohne Messtechnik GmbH'); search with a "
        "single distinctive token and a substring match, not the full phrase."
    )
    return _repo_context_cache


def build_registry() -> dict:
    """Return active tools keyed by name (Neo4j/agent tools only when configured)."""
    tools = [AasqlQueryTool(), RepoReadTool()]
    if neo4j_enabled():
        tools.extend(_agent_tools())
    else:
        log.info("NEO4J_URI unset — neo4aas agent tools disabled")
    return {t.name: t for t in tools}
