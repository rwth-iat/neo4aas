"""LangChain tool registry for chatbot_v2.

One in-process registry, read-only. The Neo4j tools are thin wrappers over the shared
``aas_mapping.aas_neo4j_adapter.agent_tools`` functions (the single source of truth that
also backs the MCP server), so behaviour matches the MCP surface exactly. ``aasql_query``
and ``repo_read`` talk to the AAS Repository REST API.
"""

import base64
import json
import re
from typing import Optional

import requests
from langchain_core.tools import tool

from config import REPOSITORY_URL, neo4j_enabled, get_aas_client, log
from llm import util_model
from system_prompt import SYSTEM_PROMPT

# Keep tool observations small. Each result can be a full AAS JSON object, so an
# unbounded list piled into the message history overflows the model context after a few
# (looping) calls — cap both the row count and the total serialized size.
_MAX_ROWS = 15
_MAX_OBS_CHARS = 4000


def _truncate(rows: list, limit: int = _MAX_ROWS) -> tuple[list, int]:
    """Return at most `limit` rows that also fit within the char budget, plus the true
    total. Large per-row objects are dropped first so the observation stays small."""
    total = len(rows)
    shown, size = [], 0
    for r in rows[:limit]:
        size += len(json.dumps(r, ensure_ascii=False, default=str))
        if shown and size > _MAX_OBS_CHARS:
            break
        shown.append(r)
    return shown, total


def _detect_target(aasql: dict) -> str:
    text = json.dumps(aasql)
    return "shells" if ('"$aas' in text or "'$aas" in text) else "submodels"


def _post_aasql(aasql: dict, target: str) -> dict:
    url = f"{REPOSITORY_URL}/query/{target}"
    try:
        resp = requests.post(url, json=aasql, timeout=30)
    except Exception as exc:  # noqa: BLE001 — surfaced to the agent as an observation
        return {"error": f"Repository unreachable: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:400]}"}
    return {"results": resp.json().get("result", [])}


def _validate_aasql(aasql: dict) -> Optional[str]:
    """Compile the AASQL with the local AASQL→Cypher compiler; return an error string if
    it is malformed, else None. Catches bad queries before they hit the repository."""
    try:
        from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import (
            convert_aasql_to_cypher,
        )
        convert_aasql_to_cypher(aasql)
        return None
    except Exception as exc:  # noqa: BLE001 — any compile failure means invalid AASQL
        return f"{type(exc).__name__}: {exc}"


def _generate_aasql(question: str, repair: Optional[str] = None) -> Optional[dict]:
    """Generate (or repair) AASQL JSON. response_format=json_object → clean JSON."""
    model = util_model().bind(response_format={"type": "json_object"})
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question}]
    if repair:
        messages.append({"role": "user", "content":
            f"The previous AASQL was invalid — the compiler rejected it with:\n{repair}\n"
            "Return a corrected AASQL JSON object."})
    msg = model.invoke(messages)
    try:
        return json.loads(msg.content)
    except (json.JSONDecodeError, TypeError):
        return None


@tool
def aasql_query(question: str) -> dict:
    """Search the AAS Repository with the AAS Query Language.

    Give a natural-language request (the user's question, verbatim — do not reword or
    narrow it). An AASQL query is generated and executed against shells/submodels. Use
    for content searches by idShort, property value, manufacturer, country, semanticId.
    """
    aasql = _generate_aasql(question)
    if aasql is None:
        return {"error": "Failed to generate valid AASQL JSON."}
    # Compose → validate (local compiler) → repair once, before hitting the repository.
    err = _validate_aasql(aasql)
    if err:
        repaired = _generate_aasql(question, repair=err)
        if repaired is not None and _validate_aasql(repaired) is None:
            aasql = repaired
    target = _detect_target(aasql)
    out = _post_aasql(aasql, target)
    # Self-correct: a cross-root query can be valid on the other endpoint.
    if "error" not in out and not out["results"]:
        other = "submodels" if target == "shells" else "shells"
        retry = _post_aasql(aasql, other)
        if "error" not in retry and retry["results"]:
            out, target = retry, other
    if "error" in out:
        return {"aasql": aasql, "target": target, "error": out["error"]}
    shown, total = _truncate(out["results"])
    return {"aasql": aasql, "target": target, "count": total, "results": shown}


_ALLOWED_PATH = re.compile(r"^/(shells|submodels|concept-descriptions)(/[^?#]*)?$")

# basyx REST identifies an AAS/Submodel/CD by its *id* (a URI), base64url-encoded into
# the path. These keywords mark where the id ends and a sub-resource begins, so an id that
# itself contains slashes (every AAS id here is a URL) isn't mistaken for path segments.
_SUBRES = {"submodel-elements", "submodel", "submodel-refs", "$value", "$metadata"}


def _b64url(seg: str) -> str:
    """base64url-encode an id segment, leaving an already-encoded segment untouched.

    A raw id is a URI (contains ``:`` / ``/`` / ``.`` — outside the base64url alphabet) so
    it is encoded; a segment already in the base64url alphabet is passed through. (An
    idShort like ``F17`` also matches the alphabet and is passed through unchanged — but an
    idShort is not a valid identifier for these endpoints; callers must pass the real id.)
    """
    if re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", seg):
        return seg
    return base64.urlsafe_b64encode(seg.encode()).decode()


def _encode_path(path: str) -> str:
    """Rewrite ``/<collection>/<rawId>[/<sub-resource>...]`` with the id base64url-encoded.

    List paths (``/shells``) and already-encoded ids pass through unchanged. The id spans
    every segment up to the first ``_SUBRES`` keyword, so URL-shaped ids survive intact.
    """
    m = re.match(r"^/(shells|submodels|concept-descriptions)(?:/(.*))?$", path)
    rest = m.group(2)
    if not rest:
        return path
    segs = rest.split("/")
    cut = next((i for i, s in enumerate(segs) if s in _SUBRES), len(segs))
    ident = _b64url("/".join(segs[:cut]))
    tail = segs[cut:]
    return "/" + m.group(1) + "/" + "/".join([ident, *tail])


@tool
def repo_read(path: str, params: Optional[dict] = None) -> dict:
    """Issue a GET to the AAS Repository REST API.

    List or fetch shells / submodels / concept-descriptions, a specific one by id, or a
    submodel's elements. Paths: /shells, /submodels, /concept-descriptions,
    /shells/{id}, /submodels/{id}, /submodels/{id}/submodel-elements.

    Pass the **id** (the URI, e.g. ``https://.../Y30`` or the ``submodel_id`` returned by
    property_values), NOT the idShort — it is base64url-encoded into the path for you. An
    idShort like ``F17`` is not a valid id here and yields 404.
    """
    path = (path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    if ".." in path or not _ALLOWED_PATH.match(path):
        return {"error": f"Path not allowed (read-only whitelist): {path}"}
    path = _encode_path(path)
    try:
        resp = requests.get(f"{REPOSITORY_URL}{path}", params=params or {}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Repository unreachable: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:400]}"}
    try:
        data = resp.json()
    except ValueError:
        return {"text": resp.text[:2000]}
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        shown, total = _truncate(data["result"])
        return {"path": path, "count": total, "results": shown}
    return {"path": path, "data": data}


# --- Neo4j-backed tools (shared agent_tools), only when a backend is configured -------

def _neo4j_tools() -> list:
    from aas_mapping.aas_neo4j_adapter import agent_tools as at

    def _run(fn, **kwargs):
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured (NEO4J_URI unset)."}
        try:
            return fn(client, **kwargs)
        except Exception as exc:  # noqa: BLE001 — return as observation, never crash the turn
            return {"error": str(exc)[:300]}

    @tool
    def cypher_read(cypher: str) -> dict:
        """Run a READ-ONLY Cypher query against the Neo4j (neo4aas) backend.

        For aggregate/graph questions the other tools don't cover: counts, distinct
        semanticIds, traversals, ECLASS/IRDI discovery. Labels: Identifiable,
        AssetAdministrationShell, Submodel, ConceptDescription, Referable,
        SubmodelElement, Property, MultiLanguageProperty, Reference. Rels: :submodels,
        :submodelElements, :value, :semanticId, :references. Reference nodes carry
        keys_value[], target_id, target_id_base (IRDI without version). RETURN explicit
        columns; writes are rejected.
        """
        out = _run(at.cypher_read, cypher=cypher)
        if isinstance(out, dict) and isinstance(out.get("rows"), list):
            shown, total = _truncate(out["rows"])
            out = {"count": total, "rows": shown}
        return out

    @tool
    def count_stats() -> dict:
        """Counts of AssetAdministrationShells, Submodels and ConceptDescriptions."""
        return _run(at.count_stats)

    @tool
    def repo_overview() -> dict:
        """Factual repository snapshot for grounding: total counts, submodel types, the
        exact manufacturers present, and all asset (AAS) idShort tags. Use for
        open-ended 'what is in the repo' questions or to learn real spellings."""
        return _run(at.repo_overview)

    @tool
    def list_submodel_types() -> dict:
        """Distinct Submodel types (idShort + semanticId) with an instance count each."""
        return _run(at.list_submodel_types)

    @tool
    def list_submodel_types_by_semantic_id() -> dict:
        """Distinct Submodel semanticIds (idShort ignored) with an instance count each."""
        return _run(at.list_submodel_types_by_semantic_id)

    @tool
    def get_identifiable(identifier: str) -> dict:
        """Fetch a top-level Identifiable (AAS/Submodel/ConceptDescription) by global id."""
        return _run(at.get_identifiable, identifier=identifier)

    @tool
    def get_referable(parent_id: str, id_short_path: Optional[str] = None) -> dict:
        """Fetch a Referable by parent id + optional idShort path (e.g. 'Coll.List[0].Prop')."""
        return _run(at.get_referable, parent_id=parent_id, id_short_path=id_short_path)

    @tool
    def abstract_submodel(submodel_type: str, output_format: str = "json") -> dict:
        """Build a Template-kind structural union of all Submodels of a given type
        (matched by semanticId, then idShort)."""
        return _run(at.abstract_submodel, submodel_type=submodel_type, output_format=output_format)

    @tool
    def validate_constraints(constraint_ids: Optional[list] = None) -> dict:
        """Validate the AAS data against the AAS spec constraints; returns a report."""
        return _run(at.validate_constraints, constraint_ids=constraint_ids)

    # --- high-level one-shot tools (correct schema baked in; no agent Cypher needed) ---

    def _tokens(field: str) -> list[str]:
        return [w.lower() for w in re.split(r"[\s_]+", field or "") if len(w) > 2]

    def _rows(cypher: str, params: dict) -> list[dict]:
        # execute_clause yields neo4j Record objects; convert to plain dicts so the tool
        # observation is clean JSON.
        client = get_aas_client()
        return [dict(r) for r in (client.execute_clause(cypher, params=params) or [])]

    # WHERE fragment: idShort contains every significant token of `field` (so
    # "degree of protection" matches Degree_of_Protection). $field stored in params.
    _IDSHORT_MATCH = ("all(tok IN $tokens WHERE toLower(n.idShort) CONTAINS tok)")
    # Value of a Property or MultiLanguageProperty (text list or scalar).
    _VAL = "coalesce(n.value_text[0], n.value)"
    # Spec-correct, dataset-agnostic asset↔element traversal: an AAS references its
    # Submodels (resolve_references() materialises :references), and a Submodel contains
    # its elements via the containment edges. The asset identity is the AAS idShort. This
    # replaces the earlier URL-tag hack (split(sm.id,'/')[-2]), which only worked because
    # this dataset encodes the asset in the submodel id; submodel ids are opaque in general.
    _JOIN = ("MATCH (a:AssetAdministrationShell)-[:submodels]->(:Reference)-[:references]->"
             "(sm:Submodel)-[:submodelElements|value|statements*1..]->(n:Referable)")
    _ASSET = "a.idShort"

    @tool
    def aggregate_field(field: str, operation: str) -> dict:
        """Aggregate a property across the whole repository in ONE call.

        Use for counts/superlatives instead of writing Cypher. `field` is a property name
        or keyword (e.g. 'ManufacturerName', 'CountryOfOrigin', 'medium temperature').
        `operation`:
          - 'count_by_value' → distinct values of the property with a DEVICE (asset) count
            each, sorted desc (answers 'which manufacturer/country has the most …', 'how
            many of each …'). Counts distinct assets, so an asset listing the value in
            several submodels is counted once.
          - 'max' / 'min' / 'avg' → numeric aggregate over the property's values (leading
            number parsed, units ignored); 'max'/'min' also report the asset and raw value
            that achieve it (answers 'highest/lowest/maximum …').
        """
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured."}
        toks = _tokens(field)
        if not toks:
            return {"error": "Empty field."}
        op = (operation or "").lower()
        if op in ("count_by_value", "count", "group", "by_value"):
            rows = _rows(
                f"{_JOIN} WHERE {_IDSHORT_MATCH} "
                f"WITH a, trim({_VAL}) AS value WHERE value IS NOT NULL AND value <> '' "
                "RETURN value, count(DISTINCT a) AS count ORDER BY count DESC LIMIT 50",
                {"tokens": toks})
            return {"field": field, "operation": "count_by_value",
                    "total_values": len(rows), "values": rows}
        if op in ("max", "min", "avg", "average", "mean", "sum"):
            rows = _rows(
                f"{_JOIN} WHERE {_IDSHORT_MATCH} "
                f"WITH {_ASSET} AS asset, n.idShort AS f, trim({_VAL}) AS raw, "
                f"toFloatOrNull(replace(split(trim({_VAL}),' ')[0], ',', '.')) AS num "
                "WHERE num IS NOT NULL "
                "RETURN asset, f AS field, raw, num ORDER BY num DESC",
                {"tokens": toks})
            if not rows:
                return {"field": field, "operation": op, "error": "No numeric values found."}
            nums = [r["num"] for r in rows]
            res = {"field": field, "operation": op, "n": len(nums)}
            if op == "max":
                res.update(value=nums[0], at=rows[0])
            elif op == "min":
                res.update(value=nums[-1], at=rows[-1])
            elif op == "sum":
                res.update(value=sum(nums))
            else:
                res.update(value=round(sum(nums) / len(nums), 4))
            return res
        return {"error": f"Unknown operation '{operation}'. Use count_by_value/max/min/avg."}

    @tool
    def property_values(field: str, asset: Optional[str] = None,
                        value_contains: Optional[str] = None,
                        value_min: Optional[float] = None,
                        value_max: Optional[float] = None) -> dict:
        """List the value of a property across assets in ONE call (projected, compact).

        `field`: property name/keyword (e.g. 'Accuracy', 'degree of protection', 'flow
        rate'). Optional filters:
          - `asset`: restrict to one asset by its idShort (e.g. 'L34').
          - `value_contains`: keep only values containing this text.
          - `value_min` / `value_max`: numeric range — keep rows whose value's leading
            number is ≥ value_min and/or ≤ value_max (use for 'flow rate above 1000',
            'lighter than 3 kg'; do NOT fetch all and filter yourself).
        Returns rows of {asset, field, value, submodel, submodel_id}. Use this for 'what is
        the X of device Y', 'list the X values', 'devices with X over N', instead of
        get_referable or hand-written Cypher. A `null` value means the property exists but
        stores no value — that IS the answer; do NOT browse /shells to "find" it. Pass
        `submodel_id` to repo_read (`/submodels/{submodel_id}`) only if you need the raw
        element.
        """
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured."}
        toks = _tokens(field)
        if not toks:
            return {"error": "Empty field."}
        where = [_IDSHORT_MATCH]
        params: dict = {"tokens": toks}
        if asset:
            where.append(f"{_ASSET} = $asset")
            params["asset"] = asset
        if value_contains:
            where.append(f"toLower({_VAL}) CONTAINS toLower($vc)")
            params["vc"] = value_contains
        num = f"toFloatOrNull(replace(split(trim({_VAL}),' ')[0], ',', '.'))"
        if value_min is not None:
            where.append(f"{num} >= $vmin")
            params["vmin"] = float(value_min)
        if value_max is not None:
            where.append(f"{num} <= $vmax")
            params["vmax"] = float(value_max)
        rows = _rows(
            f"{_JOIN} WHERE {' AND '.join(where)} "
            f"RETURN DISTINCT {_ASSET} AS asset, n.idShort AS field, {_VAL} AS value, "
            "sm.idShort AS submodel, sm.id AS submodel_id ORDER BY asset LIMIT 60",
            params)
        return {"field": field, "asset": asset, "count": len(rows), "values": rows}

    @tool
    def assets_missing(submodel_type: Optional[str] = None,
                       property: Optional[str] = None) -> dict:
        """List assets that LACK a given submodel type or property (negation/absence).

        Provide exactly one of `submodel_type` (e.g. 'TechnicalData') or `property` (a
        property name/keyword, e.g. 'CountryOfOrigin'). Returns the asset idShorts that do
        NOT have it. Use for 'which assets have no …', 'devices without a …'.
        """
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured."}
        if submodel_type:
            rows = _rows(
                "MATCH (a:AssetAdministrationShell) WITH collect(DISTINCT a.idShort) AS allA "
                "MATCH (a2:AssetAdministrationShell)-[:submodels]->(:Reference)-[:references]->"
                "(:Submodel {idShort:$t}) "
                "WITH allA, collect(DISTINCT a2.idShort) AS withA "
                "RETURN [x IN allA WHERE NOT x IN withA] AS missing",
                {"t": submodel_type})
        elif property:
            toks = _tokens(property)
            if not toks:
                return {"error": "Empty property."}
            rows = _rows(
                "MATCH (a:AssetAdministrationShell) WITH collect(DISTINCT a.idShort) AS allA "
                f"{_JOIN} WHERE {_IDSHORT_MATCH} "
                "WITH allA, collect(DISTINCT a.idShort) AS withA "
                "RETURN [x IN allA WHERE NOT x IN withA] AS missing",
                {"tokens": toks})
        else:
            return {"error": "Provide submodel_type or property."}
        missing = sorted(rows[0]["missing"]) if rows else []
        return {"criterion": submodel_type or property,
                "missing_count": len(missing), "missing_assets": missing}

    @tool
    def explain_property(field: str) -> dict:
        """Explain what an AAS property MEANS, using the loaded ECLASS dictionary.

        `field`: a property idShort (e.g. 'Max_medium_temperature') OR an ECLASS IRDI
        (e.g. '0173-1#02-AAO677#004'). Resolves the property's semanticId to its ECLASS
        ConceptDescription and returns the official preferred name, the definition, the
        unit, and the ECLASS class(es) it belongs to. Use for 'what does X mean', 'define
        X', 'what is the semantic meaning of X'.
        """
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured."}
        f = field.strip()
        is_irdi = ("#" in f) or ("/" in f)
        if is_irdi:
            base = "MATCH (cd:ConceptDescription {id_base: $base})"
            params = {"base": f.rsplit("#", 1)[0] if "#" in f else f}
            id_short = None
        else:
            # idShort → its semanticId Reference → CD (version-agnostic via id_base).
            base = ("MATCH (n:Referable) WHERE all(tok IN $tokens WHERE "
                    "toLower(n.idShort) CONTAINS tok) "
                    "MATCH (n)-[:semanticId]->(rf:Reference) "
                    "MATCH (cd:ConceptDescription {id_base: rf.target_id_base})")
            params = {"tokens": _tokens(f)}
            id_short = f
        rows = _rows(
            base + " "
            "OPTIONAL MATCH (cd)-[:HAS_UNIT]->(u:ConceptDescription) "
            "OPTIONAL MATCH (cls:ConceptDescription)-[:HAS_PROPERTY]->(cd) "
            "RETURN DISTINCT cd.id AS irdi, cd.displayName_text[0] AS name, "
            "cd.description_text[0] AS definition, "
            "collect(DISTINCT u.displayName_text[0])[..3] AS units, "
            "count(DISTINCT u) AS unit_count, "
            "collect(DISTINCT cls.displayName_text[0])[..5] AS classes, "
            "count(DISTINCT cls) AS class_count LIMIT 5",
            params)
        rows = [r for r in rows if r.get("name") or r.get("definition")]
        if not rows:
            return {"field": field, "found": False,
                    "note": "No ECLASS definition found for this property (its semanticId "
                            "may not be an ECLASS concept in the loaded segment 27)."}
        return {"field": field, "found": True, "matched_idShort": id_short,
                "meanings": rows}

    @tool
    def find_by_eclass_concept(irdi: str) -> dict:
        """Find every AAS element tagged with an ECLASS concept (version-agnostic discovery).

        `irdi`: an ECLASS IRDI (e.g. '0173-1#02-AAO677#004' or without the version
        '0173-1#02-AAO677'). Matches on the version-agnostic base, so elements tagged with
        any version of the concept are returned. Use for 'find everything that means X',
        'all elements with semanticId Y'. Returns {asset, field, value} rows.
        """
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured."}
        base = irdi.strip().rsplit("#", 1)[0] if "#" in irdi else irdi.strip()
        rows = _rows(
            f"{_JOIN}-[:semanticId]->(rf:Reference) WHERE rf.target_id_base = $base "
            f"RETURN DISTINCT {_ASSET} AS asset, n.idShort AS field, {_VAL} AS value, "
            "sm.idShort AS submodel ORDER BY asset LIMIT 80",
            {"base": base})
        names = _rows("MATCH (cd:ConceptDescription {id_base:$b}) "
                      "RETURN cd.displayName_text[0] AS name LIMIT 1", {"b": base})
        return {"concept": base, "eclass_name": names[0]["name"] if names else None,
                "count": len(rows), "elements": rows}

    @tool
    def asset_components(asset: str) -> dict:
        """Return the components / bill-of-materials of an asset from its
        HierarchicalStructures submodel, in ONE call.

        `asset`: the asset idShort (e.g. 'N13', 'N18'). Returns the child component asset
        idShorts and the part-of relationships. Use for 'what is the BOM of …', 'which
        components belong to …'.
        """
        client = get_aas_client()
        if client is None:
            return {"error": "Neo4j backend not configured."}
        rows = _rows(
            "MATCH (a:AssetAdministrationShell {idShort:$a})-[:submodels]->(:Reference)"
            "-[:references]->(sm:Submodel {idShort:'HierarchicalStructures'}) "
            "MATCH (sm)-[:submodelElements|value|statements*1..]->(e:Referable) "
            "WHERE e.idShort IS NOT NULL "
            "RETURN DISTINCT e.idShort AS element, labels(e)[0] AS kind LIMIT 60",
            {"a": asset})
        comps = [r["element"] for r in rows
                 if r["element"] not in ("ArcheType", "EntryNode")
                 and not r["element"].startswith("HasPart")
                 and not r["element"].startswith("IsPartOf")]
        rels = [r["element"] for r in rows if r["element"].startswith(("HasPart", "IsPartOf"))]
        return {"asset": asset, "components": comps, "relationships": rels,
                "found": bool(rows)}

    return [cypher_read, count_stats, repo_overview, list_submodel_types,
            list_submodel_types_by_semantic_id, get_identifiable, get_referable,
            abstract_submodel, validate_constraints,
            aggregate_field, property_values, asset_components, assets_missing,
            explain_property, find_by_eclass_concept]


@tool
def find_relevant_fields(question: str) -> dict:
    """Discover the real AAS field names relevant to a question (semantic search).

    Pass the user's ORIGINAL question VERBATIM — do NOT reword, translate, or shorten it.
    The tool itself expands it into candidate field names and searches by meaning, so it
    finds the right idShorts even when the question wording differs from them. Returns the
    closest SubmodelElement fields (idShort, Submodel type, semanticId). Use this BEFORE
    aasql_query when unsure how a property is named, then target those exact names.
    """
    from retrieval import find_relevant_fields as _find
    return _find(question)


def build_tools() -> list:
    """Active LangChain tools (Neo4j tools only when a backend is configured)."""
    tools = [aasql_query, repo_read]
    if neo4j_enabled():
        tools.extend(_neo4j_tools())
        tools.append(find_relevant_fields)
    else:
        log.info("NEO4J_URI unset — neo4aas tools disabled")
    return tools


_repo_context_cache: Optional[str] = None


def repo_context_text() -> str:
    """Compact factual repository profile injected into the agent prompt for grounding.
    Built once from repo_overview; empty when no Neo4j backend is configured."""
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
    except Exception as exc:  # noqa: BLE001
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
