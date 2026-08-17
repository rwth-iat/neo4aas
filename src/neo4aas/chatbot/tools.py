"""LangChain tool registry for chatbot_v2.

One in-process registry, read-only. The Neo4j tools are thin wrappers over the shared
``neo4aas.agent_tools`` functions (the single source of truth that
also backs the MCP server), so behaviour matches the MCP surface exactly. ``aasql_query``
and ``repo_read`` talk to the AAS Repository REST API.
"""

import base64
import csv
import io
import json
import re
from typing import Optional

import requests
from langchain_core.tools import tool

from neo4aas.core.utils import irdi_base
from .config import neo4j_enabled, get_aas_client, get_repo, log
from .llm import util_model
from .system_prompt import SYSTEM_PROMPT

# Keep tool observations small. Each result can be a full AAS JSON object, so an
# unbounded list piled into the message history overflows the model context after a few
# (looping) calls. The char budget is the real limiter; the row cap is only a loose safety
# ceiling so short rows (e.g. bare idShorts) aren't undercounted — many fit within the char
# budget, while fat AAS-JSON rows still stop at ~_MAX_OBS_CHARS.
_MAX_ROWS = 200
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


def _sem_id(obj: dict) -> str:
    """A Reference dict's key values joined with '|' (single key → the bare value, missing
    → ''). The full AAS JSON carries semanticId as {type, keys:[{value, type}]}."""
    ref = obj.get("semanticId") or {}
    vals = [k.get("value", "") for k in ref.get("keys", []) if k.get("value")]
    return "|".join(vals)


def _mlp_text(value):
    """Flatten a MultiLanguageProperty value to one string, preferring en* then de* then the
    first text. The REST JSON shape is a list of {language, text}; a scalar passes through."""
    if not isinstance(value, list):
        return value
    texts = [(e.get("language", ""), e.get("text", "")) for e in value if isinstance(e, dict)]
    if not texts:
        return None
    for pref in ("en", "de"):
        for lang, text in texts:
            if lang.lower().startswith(pref):
                return text
    return texts[0][1]


def _project_row(obj: dict, target: str) -> dict:
    """Compact identity row for one query result. Submodels carry a semanticId; shells carry
    a globalAssetId instead (shells have no semanticId)."""
    row = {"modelType": obj.get("modelType"), "idShort": obj.get("idShort"),
           "id": obj.get("id")}
    if target == "shells":
        row["globalAssetId"] = (obj.get("assetInformation") or {}).get("globalAssetId")
    else:
        row["semanticId"] = _sem_id(obj)
    return row


def _project_elements(obj: dict) -> list:
    """Top-level submodelElements projected to {idShort, modelType, value}. Property → scalar
    value, MultiLanguageProperty → language-preferred text, Collection/List → None (shallow,
    no recursion — bounds the size)."""
    out = []
    for se in obj.get("submodelElements", []):
        mt = se.get("modelType")
        if mt == "MultiLanguageProperty":
            val = _mlp_text(se.get("value"))
        elif mt in ("SubmodelElementCollection", "SubmodelElementList"):
            val = None
        else:
            val = se.get("value")
        out.append({"idShort": se.get("idShort"), "modelType": mt, "value": val})
    return out


def _rows_to_csv(rows: list) -> str:
    """Serialize projected dict rows to CSV (header + rows). DictWriter quotes any cell
    containing a comma or the '|' semanticId separator."""
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _cap_list_field(out: dict, key: str, limit: int = _MAX_ROWS) -> dict:
    """Cap an unbounded list field in a tool result so it can't overflow the model
    context. Some agent_tools (list_submodel_types, assets_missing) return a list whose
    length scales with the repository — on the ~9k-AAS supplier repo `list_submodel_types`
    returns 7801 hash-suffixed idShorts (~410k tokens) and `assets_missing` thousands of
    idShorts (~140k tokens), both of which exceed the 131k model window and 400 the run.
    Keep at most `limit` items, drop the rest, and add a `{key}_total`/`truncated` marker so
    the count stays honest. The full count is already reported separately (total_types /
    missing_count)."""
    if isinstance(out, dict) and isinstance(out.get(key), list) and len(out[key]) > limit:
        total = len(out[key])
        out = {**out, key: out[key][:limit], f"{key}_total": total, "truncated": True}
    return out


def _detect_target(aasql: dict) -> str:
    text = json.dumps(aasql)
    return "shells" if ('"$aas' in text or "'$aas" in text) else "submodels"


def _post_aasql(aasql: dict, target: str, base_url: str) -> dict:
    url = f"{base_url}/query/{target}"
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
        from neo4aas.core.query.aasql_to_cypher import (
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


def _run_aasql_query(question: str, base_url: str, verbosity: str = "summary",
                     include_elements: bool = False) -> dict:
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
    out = _post_aasql(aasql, target, base_url)
    # Self-correct: a cross-root query can be valid on the other endpoint.
    if "error" not in out and not out["results"]:
        other = "submodels" if target == "shells" else "shells"
        retry = _post_aasql(aasql, other, base_url)
        if "error" not in retry and retry["results"]:
            out, target = retry, other
    if "error" in out:
        return {"aasql": aasql, "target": target, "error": out["error"]}

    if verbosity == "full":
        # Complete AAS JSON objects (capped). Use when the agent needs every field.
        shown, total = _truncate(out["results"])
        return {"aasql": aasql, "target": target, "count": total,
                "truncated": total > len(shown), "results": shown}

    # Summary: compact identity rows so most/all results fit one observation.
    if include_elements:
        # Nested elements can't be expressed as CSV → return compact JSON rows.
        rows = [{**_project_row(o, target), "elements": _project_elements(o)}
                for o in out["results"]]
        shown, total = _truncate(rows)
        return {"aasql": aasql, "target": target, "count": total,
                "truncated": total > len(shown), "results": shown}
    rows = [_project_row(o, target) for o in out["results"]]
    shown, total = _truncate(rows)
    return {"aasql": aasql, "target": target, "count": total,
            "truncated": total > len(shown), "format": "csv", "rows": _rows_to_csv(shown)}


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


def _run_repo_read(path: str, params: Optional[dict], base_url: str) -> dict:
    path = (path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    if ".." in path or not _ALLOWED_PATH.match(path):
        return {"error": f"Path not allowed (read-only whitelist): {path}"}
    path = _encode_path(path)
    try:
        resp = requests.get(f"{base_url}{path}", params=params or {}, timeout=30)
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

def _neo4j_tools(repo) -> list:
    from neo4aas import agent_tools as at

    def _run(fn, **kwargs):
        client = get_aas_client(repo.id)
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
        exact manufacturers present, and a sample of asset (AAS) idShort tags. Use for
        open-ended 'what is in the repo' questions or to learn real spellings. The
        submodel-type and asset-name lists are capped (totals reported) so the snapshot
        stays bounded on large repositories — use list_submodel_types_by_semantic_id for a
        full type breakdown, or aggregate_field for full manufacturer/value counts."""
        out = _run(at.repo_overview)
        # repo_overview embeds the full by-idShort type list (thousands when idShorts are
        # per-instance suffixed) plus every asset idShort (~9k on the supplier repo): ~1.3M
        # chars / ~410k tokens, which alone overflows the 131k model window. Cap each list.
        out = _cap_list_field(out, "submodel_types")
        out = _cap_list_field(out, "asset_names", limit=40)
        out = _cap_list_field(out, "manufacturers", limit=40)
        return out

    @tool
    def list_submodel_types() -> dict:
        """Distinct Submodel types (idShort + semanticId) with an instance count each.

        Note: groups by idShort, which can be in the thousands when sources suffix the
        idShort per instance — prefer `list_submodel_types_by_semantic_id` for a clean
        type overview. The result list is capped; `total_types` reports the true count."""
        return _cap_list_field(_run(at.list_submodel_types), "types")

    @tool
    def list_submodel_types_by_semantic_id() -> dict:
        """Distinct Submodel semanticIds (idShort ignored) with an instance count each.
        Preferred for 'what submodel types exist' — collapses per-instance idShort noise."""
        return _cap_list_field(_run(at.list_submodel_types_by_semantic_id), "types")

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

    def _is_number(v) -> bool:
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False

    def _rows(cypher: str, params: dict) -> list[dict]:
        # execute_clause yields neo4j Record objects; convert to plain dicts so the tool
        # observation is clean JSON.
        client = get_aas_client(repo.id)
        return [dict(r) for r in (client.execute_clause(cypher, params=params) or [])]

    # WHERE fragment: idShort contains every significant token of `field` (so
    # "degree of protection" matches Degree_of_Protection). $field stored in params.
    _IDSHORT_MATCH = ("all(tok IN $tokens WHERE toLower(n.idShort) CONTAINS tok)")

    def _lang_val(v: str = "n") -> str:
        """Cypher expression for the value of a Property or MultiLanguageProperty, picking a
        stable language for MLPs instead of the arbitrary first entry.

        A Property stores its scalar in `value` (no `value_text`); a MultiLanguageProperty
        flattens to parallel `value_text[]` / `value_language[]`. `value_text[0]` therefore
        returned whatever language happened to be first (e.g. a Chinese designation). Prefer
        English, then German, then fall back to the first text. The language lookup is two
        tiny list comprehensions over the (1–3 element) language array — evaluated per row but
        negligible, no extra MATCH/traversal, so the tool is no slower in practice."""
        rng = f"range(0, size({v}.value_text)-1)"
        guard = (f"{v}.value_language IS NOT NULL AND i < size({v}.value_language) "
                 f"AND toLower({v}.value_language[i]) STARTS WITH ")
        return (
            f"CASE WHEN {v}.value_text IS NULL THEN {v}.value ELSE coalesce("
            f"head([i IN {rng} WHERE {guard}'en' | {v}.value_text[i]]), "
            f"head([i IN {rng} WHERE {guard}'de' | {v}.value_text[i]]), "
            f"{v}.value_text[0], {v}.value) END"
        )

    # Value of a Property or MultiLanguageProperty (language-preferring: en > de > first).
    _VAL = _lang_val("n")
    # Leading number of a value (units ignored, comma decimal normalised), null if non-numeric.
    _NUM = f"toFloatOrNull(replace(split(trim({_VAL}),' ')[0], ',', '.'))"
    # Spec-correct, dataset-agnostic asset↔element traversal: an AAS references its
    # Submodels (resolve_references() materialises :references), and a Submodel contains
    # its elements via the containment edges. The asset identity is the AAS idShort. This
    # replaces the earlier URL-tag hack (split(sm.id,'/')[-2]), which only worked because
    # this dataset encodes the asset in the submodel id; submodel ids are opaque in general.
    _JOIN = ("MATCH (a:AssetAdministrationShell)-[:submodels]->(:Reference)-[:references]->"
             "(sm:Submodel)-[:submodelElements|value|statements*1..]->(n:Referable)")
    _ASSET = "a.idShort"

    @tool
    def aggregate_field(field: Optional[str] = None, operation: str = "max",
                        semantic_id: Optional[str] = None) -> dict:
        """Aggregate a property across the whole repository in ONE call.

        Use for counts/superlatives instead of writing Cypher. Identify the property by
        EITHER:
          - `semantic_id`: an ECLASS IRDI (e.g. '0173-1#02-AAC971'). PREFER THIS — it matches
            the concept version-agnostically, so it unifies every vendor/language idShort of
            the same property (e.g. 'Max_flow_rate' AND the German 'max_Durchfluss', which
            share one semanticId). An idShort search would aggregate only one spelling and
            silently miss the others.
          - `field`: a property name/keyword (e.g. 'ManufacturerName', 'medium temperature').
            idShort token match — use only when no semanticId is known.
        `operation`:
          - 'count_by_value' → distinct values of the property with a DEVICE (asset) count
            each, sorted desc (answers 'which manufacturer/country has the most …', 'how
            many of each …'). Counts distinct assets, so an asset listing the value in
            several submodels is counted once.
          - 'max' / 'min' / 'avg' → numeric aggregate over the property's values (leading
            number parsed, units ignored); 'max'/'min' also report the asset and raw value
            that achieve it (answers 'highest/lowest/maximum …').
        """
        client = get_aas_client(repo.id)
        if client is None:
            return {"error": "Neo4j backend not configured."}
        # Resolve the element-selection MATCH+WHERE: semanticId (preferred) or idShort tokens.
        if semantic_id:
            base = irdi_base(semantic_id.strip())
            sel = f"{_JOIN}-[:semanticId]->(rf:Reference) WHERE rf.target_id_base = $base"
            params: dict = {"base": base}
            label = {"semantic_id": base}
        else:
            toks = _tokens(field or "")
            if not toks:
                return {"error": "Provide field or semantic_id."}
            sel = f"{_JOIN} WHERE {_IDSHORT_MATCH}"
            params = {"tokens": toks}
            label = {"field": field}
        op = (operation or "").lower()
        if op in ("count_by_value", "count", "group", "by_value"):
            rows = _rows(
                f"{sel} "
                f"WITH a, trim({_VAL}) AS value WHERE value IS NOT NULL AND value <> '' "
                "RETURN value, count(DISTINCT a) AS count ORDER BY count DESC LIMIT 50",
                params)
            return {**label, "operation": "count_by_value",
                    "total_values": len(rows), "values": rows}
        if op in ("max", "min", "avg", "average", "mean", "sum"):
            rows = _rows(
                f"{sel} "
                f"WITH {_ASSET} AS asset, n.idShort AS f, trim({_VAL}) AS raw, "
                f"{_NUM} AS num "
                "WHERE num IS NOT NULL "
                "RETURN asset, f AS field, raw, num ORDER BY num DESC",
                params)
            if not rows:
                return {**label, "operation": op, "error": "No numeric values found."}
            nums = [r["num"] for r in rows]
            res = {**label, "operation": op, "n": len(nums)}
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
        client = get_aas_client(repo.id)
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
        if value_min is not None:
            where.append(f"{_NUM} >= $vmin")
            params["vmin"] = float(value_min)
        if value_max is not None:
            where.append(f"{_NUM} <= $vmax")
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
        client = get_aas_client(repo.id)
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
        # Cap the idShort list: on the supplier repo a submodel type absent from ~8k assets
        # returns ~8k idShorts (~140k tokens) and overflows the model window. The full count
        # is in missing_count; a sample is enough for the agent to answer.
        return _cap_list_field(
            {"criterion": submodel_type or property,
             "missing_count": len(missing), "missing_assets": missing},
            "missing_assets")

    @tool
    def explain_property(field: str) -> dict:
        """Explain what an AAS property MEANS, using the loaded ECLASS dictionary.

        `field`: a property idShort (e.g. 'Max_medium_temperature') OR an ECLASS IRDI
        (e.g. '0173-1#02-AAO677#004'). Resolves the property's semanticId to its ECLASS
        ConceptDescription and returns the official preferred name, the definition, the
        unit, and the ECLASS class(es) it belongs to. Use for 'what does X mean', 'define
        X', 'what is the semantic meaning of X'.
        """
        client = get_aas_client(repo.id)
        if client is None:
            return {"error": "Neo4j backend not configured."}
        f = field.strip()
        is_irdi = ("#" in f) or ("/" in f)
        if is_irdi:
            base = "MATCH (cd:ConceptDescription {id_base: $base})"
            params = {"base": irdi_base(f)}
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

    # Map a criterion operator to a Cypher predicate over the element value. Numeric ops
    # compare the leading number (_NUM); contains/= are text. $v<i> holds the bound value.
    def _sem_pred(op: str, vparam: str) -> str:
        o = (op or ">=").strip().lower()
        if o in (">=", "ge", "min"):       return f"{_NUM} >= ${vparam}"
        if o in ("<=", "le", "max"):       return f"{_NUM} <= ${vparam}"
        if o in (">", "gt"):               return f"{_NUM} > ${vparam}"
        if o in ("<", "lt"):               return f"{_NUM} < ${vparam}"
        if o in ("contains", "~"):         return f"toLower({_VAL}) CONTAINS toLower(${vparam})"
        if o in ("=", "==", "eq"):
            # numeric equality when the bound value parses as a number, else text equality.
            return (f"(({_NUM} = ${vparam}_n) OR (toLower(trim({_VAL})) = "
                    f"toLower(trim(toString(${vparam})))))")
        raise ValueError(f"Unknown op '{op}'. Use >=,<=,>,<,=,contains.")

    _NUMERIC_OPS = {">=", "ge", "min", "<=", "le", "max", ">", "gt", "<", "lt"}

    @tool
    def find_submodel_elements_by_semantic_id(
            semantic_id: str, value_min: Optional[float] = None,
            value_max: Optional[float] = None, value_contains: Optional[str] = None,
            asset: Optional[str] = None) -> dict:
        """Find AAS elements by their semanticId (IRDI) — the stable, vendor/language-agnostic
        way to query a property. PREFER THIS over property_values/aggregate_field when the
        user gives a semanticId/IRDI (e.g. '0173-1#02-BAA039#010'): the same concept has
        DIFFERENT idShorts across vendors/languages (e.g. 'MaxAmbientTemperature' vs the
        German 'max_Umgebungstemperatur'), so an idShort search silently misses some assets;
        the semanticId unifies them.

        `semantic_id`: an IRDI, with or without the trailing version (matched version-
        agnostically on the base, so any version of the concept matches). Optional filters:
          - `value_min` / `value_max`: numeric range on the value's leading number
            (e.g. value_min=100 for '≥ 100 °C'); units are ignored.
          - `value_contains`: keep only values containing this text.
          - `asset`: restrict to one asset by idShort.
        Returns {asset, field, value, submodel} rows (one per matching element).
        """
        client = get_aas_client(repo.id)
        if client is None:
            return {"error": "Neo4j backend not configured."}
        base = irdi_base(semantic_id.strip())
        where = ["rf.target_id_base = $base"]
        params: dict = {"base": base}
        if value_min is not None:
            where.append(f"{_NUM} >= $vmin"); params["vmin"] = float(value_min)
        if value_max is not None:
            where.append(f"{_NUM} <= $vmax"); params["vmax"] = float(value_max)
        if value_contains:
            where.append(f"toLower({_VAL}) CONTAINS toLower($vc)"); params["vc"] = value_contains
        if asset:
            where.append(f"{_ASSET} = $asset"); params["asset"] = asset
        rows = _rows(
            f"{_JOIN}-[:semanticId]->(rf:Reference) WHERE {' AND '.join(where)} "
            f"RETURN DISTINCT {_ASSET} AS asset, n.idShort AS field, {_VAL} AS value, "
            "sm.idShort AS submodel ORDER BY asset LIMIT 80",
            params)
        names = _rows("MATCH (cd:ConceptDescription {id_base:$b}) "
                      "RETURN cd.displayName_text[0] AS name LIMIT 1", {"b": base})
        return {"semantic_id": base, "eclass_name": names[0]["name"] if names else None,
                "count": len(rows), "elements": rows}

    @tool
    def find_assets_by_semantic_criteria(criteria: list[dict]) -> dict:
        """Find assets whose elements satisfy ALL of several semanticId criteria (AND).

        Use for requirement matching by IRDI — 'a sensor that measures from ≤ -40 °C AND up
        to ≥ 120 °C AND tolerates ≥ 30 bar AND ambient ≥ 100 °C'. Each criterion is a dict
        ``{"semantic_id": "<IRDI>", "op": "<>=|<=|>|<|=|contains>", "value": <number|str>}``
        (op defaults to '>='). Because it keys on semanticId, it unifies vendor/language
        idShort differences that an idShort search would miss. Returns only assets meeting
        EVERY criterion, with the matched value per criterion.
        """
        client = get_aas_client(repo.id)
        if client is None:
            return {"error": "Neo4j backend not configured."}
        if not criteria:
            return {"error": "Provide at least one criterion."}
        per: list[dict[str, dict]] = []  # criterion index -> {asset: value}
        labels: list[str] = []
        for i, crit in enumerate(criteria):
            sid = str(crit.get("semantic_id") or crit.get("irdi") or "").strip()
            if not sid:
                return {"error": f"Criterion {i} has no semantic_id."}
            op = crit.get("op", ">=")
            val = crit.get("value")
            base = irdi_base(sid)
            params: dict = {"base": base}
            preds = ["rf.target_id_base = $base"]
            if val is not None:
                preds.append(_sem_pred(op, "v"))
                params["v"] = float(val) if str(op).strip().lower() in _NUMERIC_OPS else val
                if str(op).strip().lower() in ("=", "==", "eq"):
                    params["v_n"] = float(val) if _is_number(val) else None
            rows = _rows(
                f"{_JOIN}-[:semanticId]->(rf:Reference) WHERE {' AND '.join(preds)} "
                f"RETURN DISTINCT {_ASSET} AS asset, {_VAL} AS value",
                params)
            per.append({r["asset"]: r["value"] for r in rows})
            labels.append(f"{base} {op} {val}")
        common = set(per[0])
        for m in per[1:]:
            common &= set(m)
        assets = [{"asset": a, "matched": {labels[i]: per[i].get(a) for i in range(len(per))}}
                  for a in sorted(common)]
        out = {"criteria": labels, "match_count": len(assets), "assets": assets}
        return _cap_list_field(out, "assets")

    @tool
    def asset_components(asset: str) -> dict:
        """Return the components / bill-of-materials of an asset from its
        HierarchicalStructures submodel, in ONE call.

        `asset`: the asset idShort (e.g. 'N13', 'N18'). Returns the child component asset
        idShorts and the part-of relationships. Use for 'what is the BOM of …', 'which
        components belong to …'.
        """
        client = get_aas_client(repo.id)
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

    @tool
    def get_asset(asset: str) -> dict:
        """Look up ONE asset (AAS) by its idShort OR its id (URI) and return a compact card.

        `asset`: the asset's **id** (the globally-unique AAS id URI — preferred, unambiguous)
        or its **idShort** (exact or partial, e.g. 'ABB_Actuators_310320262157'). idShort is
        NOT unique — several AAS can share one — so when the lookup is ambiguous this returns
        `{ambiguous: True, count, matches:[{asset, aas_id}]}` instead of guessing; re-call with
        the chosen `aas_id`. On a unique match returns `{asset, aas_id, global_asset_id,
        manufacturer, designation, submodels:[{type, id}]}` — the AAS id, its manufacturer/
        product designation, and its submodels (type idShort + the id to pass to repo_read).
        Use for 'show details of asset X', 'manufacturer of X', 'which submodels does X have'
        instead of aasql_query + repo_read (which over-fetch a large subgraph and loop). The
        `id` of a listed submodel is the key for repo_read('/submodels/{id}').
        """
        client = get_aas_client(repo.id)
        if client is None:
            return {"error": "Neo4j backend not configured."}
        # Resolve candidates first (id is unique; idShort may collide). Rank: exact id (0),
        # exact idShort (1), partial idShort (2); keep only the best tier present.
        cands = _rows(
            "MATCH (a:AssetAdministrationShell) "
            "WHERE a.id = $q OR a.idShort = $q OR toLower(a.idShort) CONTAINS toLower($q) "
            "RETURN a.id AS aas_id, a.idShort AS asset, "
            "CASE WHEN a.id = $q THEN 0 WHEN a.idShort = $q THEN 1 ELSE 2 END AS rank "
            "ORDER BY rank, asset LIMIT 51",
            {"q": asset})
        if not cands:
            return {"asset": asset, "found": False,
                    "note": "No AssetAdministrationShell with that id or idShort."}
        best = min(c["rank"] for c in cands)
        top = [c for c in cands if c["rank"] == best]
        if len(top) > 1:
            # idShort collision (or a partial term matching several): don't pick one silently.
            shown = [{"asset": c["asset"], "aas_id": c["aas_id"]} for c in top[:_MAX_ROWS]]
            return {"asset": asset, "found": True, "ambiguous": True,
                    "count": len(top), "matches": shown,
                    "note": "idShort is not unique; re-call get_asset with the chosen aas_id."}
        aas_id = top[0]["aas_id"]
        # Detailed card keyed by the unique id.
        rows = _rows(
            "MATCH (a:AssetAdministrationShell {id: $id}) "
            "RETURN a.idShort AS asset, a.id AS aas_id, a.globalAssetId AS global_asset_id, "
            "[(a)-[:submodels]->(:Reference)-[:references]->(sm:Submodel) "
            "  | {type: sm.idShort, id: sm.id}] AS submodels, "
            "[(a)-[:submodels]->(:Reference)-[:references]->(:Submodel)"
            "-[:submodelElements|value|statements*1..]->(mn:Referable) "
            f"  WHERE mn.idShort = 'ManufacturerName' | {_lang_val('mn')}] AS mfg, "
            # Fallback: some suppliers (e.g. ABB) carry the maker as 'Company' in
            # ContactInformations rather than a Nameplate ManufacturerName.
            "[(a)-[:submodels]->(:Reference)-[:references]->(:Submodel)"
            "-[:submodelElements|value|statements*1..]->(co:Referable) "
            f"  WHERE co.idShort = 'Company' | {_lang_val('co')}] AS company, "
            "[(a)-[:submodels]->(:Reference)-[:references]->(:Submodel)"
            "-[:submodelElements|value|statements*1..]->(d:Referable) "
            "  WHERE d.idShort = 'ManufacturerProductDesignation' "
            f"  | {_lang_val('d')}] AS dsg",
            {"id": aas_id})
        r = rows[0]
        # dedup submodels and pick the first non-null manufacturer/designation
        seen, subs = set(), []
        for s in r["submodels"]:
            k = (s.get("type"), s.get("id"))
            if k not in seen:
                seen.add(k)
                subs.append(s)
        mfg = next((m for m in r["mfg"] if m), None) or next((c for c in r["company"] if c), None)
        dsg = next((d for d in r["dsg"] if d), None)
        return {"asset": r["asset"], "found": True, "aas_id": r["aas_id"],
                "global_asset_id": r["global_asset_id"], "manufacturer": mfg,
                "designation": dsg, "submodels": subs}

    return [cypher_read, count_stats, repo_overview, list_submodel_types,
            list_submodel_types_by_semantic_id, get_identifiable, get_referable,
            abstract_submodel, validate_constraints,
            aggregate_field, property_values, asset_components, assets_missing,
            explain_property, find_submodel_elements_by_semantic_id,
            find_assets_by_semantic_criteria, get_asset]


def build_tools(repo=None) -> list:
    """Active LangChain tools bound to one repository (Neo4j tools only when it has a backend).

    `repo` is a config.RepoConfig; defaults to the default repo (used e.g. for building the
    repo-independent tool-description map). Tools close over `repo` so each agent queries its
    own REST + Neo4j backend.
    """
    if repo is None:
        repo = get_repo(None)

    @tool
    def aasql_query(question: str, verbosity: str = "summary",
                    include_elements: bool = False) -> dict:
        """Search the AAS Repository with the AAS Query Language.

        Give a natural-language request describing the data to find — keep the user's intent
        intact (don't distort it); when listing by submodel type you may first narrow with
        list_submodel_types_by_semantic_id. An AASQL query is generated and executed against
        shells/submodels. Use for content searches by idShort, property value, manufacturer,
        country, semanticId.

        Returns (default ``verbosity='summary'``) compact CSV rows — modelType, idShort, id,
        semanticId (globalAssetId for shells) — so 'which/list' questions are answered in one
        call; read the asset identity straight from the rows. Pass ``verbosity='full'`` for
        the complete AAS JSON objects (then repo_read by id for detail). For submodels,
        ``include_elements=True`` embeds compact submodelElements ({idShort, modelType,
        value}) as JSON.
        """
        return _run_aasql_query(question, repo.repository_url, verbosity, include_elements)

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
        return _run_repo_read(path, params, repo.repository_url)

    @tool
    def find_relevant_fields(question: str) -> dict:
        """Discover the real AAS field names relevant to a question (semantic search).

        Pass the user's ORIGINAL question VERBATIM — do NOT reword, translate, or shorten it.
        The tool itself expands it into candidate field names and searches by meaning, so it
        finds the right idShorts even when the question wording differs from them. Returns the
        closest SubmodelElement fields (idShort, Submodel type, semanticId). Use this BEFORE
        aasql_query when unsure how a property is named, then target those exact names.
        """
        from .retrieval import find_relevant_fields as _find
        return _find(question, repo.id)

    tools = [aasql_query, repo_read]
    if neo4j_enabled(repo.id):
        tools.extend(_neo4j_tools(repo))
        tools.append(find_relevant_fields)
    else:
        log.info("Repo '%s' has no Neo4j backend — neo4aas tools disabled", repo.id)
    return tools


_repo_context_cache: dict[str, str] = {}  # repo_id -> grounding text


def repo_context_text(repo) -> str:
    """Compact factual repository profile injected into the agent prompt for grounding.
    Built once per repo from repo_overview; empty when that repo has no Neo4j backend."""
    if repo.id in _repo_context_cache:
        return _repo_context_cache[repo.id]
    client = get_aas_client(repo.id)
    if client is None:
        _repo_context_cache[repo.id] = ""
        return ""
    try:
        from neo4aas import agent_tools as at
        ov = at.repo_overview(client)
    except Exception as exc:  # noqa: BLE001
        log.warning("repo_overview failed, no repo context: %s", exc)
        _repo_context_cache[repo.id] = ""
        return ""
    c = ov["counts"]

    def _capped(items: list[str], n: int) -> str:
        # Bound the grounding text: a large catalog (e.g. Lieferanten has thousands of
        # distinct submodel idShorts) would otherwise overflow the model context.
        extra = len(items) - n
        return ", ".join(items[:n]) + (f", … (+{extra} more)" if extra > 0 else "")

    # Most-frequent submodel types first, then cap.
    sm_types = sorted(ov["submodel_types"], key=lambda t: t.get("count", 0), reverse=True)
    types = _capped([f"{t['idShort']} ({t['count']})" for t in sm_types], 40)
    mans = _capped(ov["manufacturers"], 40)
    assets = _capped(ov["asset_names"], 60)
    _repo_context_cache[repo.id] = (
        f"REPOSITORY: {repo.label}\n"
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
    return _repo_context_cache[repo.id]
