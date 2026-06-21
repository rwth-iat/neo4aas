import json
import time
from collections import deque

import requests
from flask import Flask, jsonify, request, send_from_directory

from config import REPOSITORY_URL, MODEL_SMALL, log
from llm import llm_call
import orchestrator

app = Flask(__name__, static_folder="static")

LOG_BUFFER: deque = deque(maxlen=200)


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _detect_target(aasql: dict) -> str:
    text = json.dumps(aasql or {})
    return "shells" if ('"$aas' in text or "'$aas" in text) else "submodels"


def _query_repository(aasql: dict, target: str) -> tuple[list[dict], int, str | None]:
    url = f"{REPOSITORY_URL}/query/{target}"
    t0 = time.perf_counter()
    try:
        resp = requests.post(url, json=aasql, timeout=30)
    except Exception as exc:
        return [], int((time.perf_counter() - t0) * 1000), f"Repository unreachable: {exc}"
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if resp.status_code >= 400:
        return [], elapsed_ms, f"HTTP {resp.status_code}: {resp.text}"
    return resp.json().get("result", []), elapsed_ms, None


def _result_preview(r: dict) -> dict:
    preview = {
        "idShort":   r.get("idShort", ""),
        "id":        r.get("id", ""),
        "modelType": r.get("modelType", ""),
    }
    if isinstance(r.get("assetInformation"), dict):
        preview["globalAssetId"] = r["assetInformation"].get("globalAssetId", "")
        preview["assetKind"]     = r["assetInformation"].get("assetKind", "")
    flat: dict[str, str] = {}

    def walk(node, depth=0):
        if depth > 4 or not isinstance(node, dict):
            return
        key = node.get("idShort")
        val = node.get("value")
        mtype = node.get("modelType", "")
        if key and isinstance(val, (str, int, float, bool)):
            flat.setdefault(key, str(val))
        if mtype == "SubmodelElementCollection" and isinstance(val, list):
            for child in val:
                walk(child, depth + 1)
        if mtype == "MultiLanguageProperty" and isinstance(val, list):
            txts = [v.get("text", "") for v in val if isinstance(v, dict)]
            if txts:
                flat.setdefault(key or "mlp", "; ".join(t for t in txts if t))

    if isinstance(r.get("submodelElements"), list):
        for el in r["submodelElements"]:
            walk(el)
    for k in ("ManufacturerProductDesignation", "ManufacturerName", "CountryOfOrigin",
              "OrderCodeOfManufacturer", "ProductArticleNumberOfManufacturer",
              "MaxOperatingTemperature", "MaxOperatingPressure", "NominalVoltage", "RatedPower"):
        if k in flat:
            preview[k] = flat[k]
    return preview


def _explain_results(user_message: str, results: list[dict], target: str) -> tuple[str, int]:
    if not results:
        return "No matching assets found in the repository.", 0
    previews = [_result_preview(r) for r in results[:15]]
    summary = json.dumps(previews, indent=2)
    if len(results) > 15:
        summary += f"\n... and {len(results) - 15} more"
    prompt = (
        f'The user asked: "{user_message}"\n\n'
        f"The AASQL query returned {len(results)} {target}. Compact previews of up to 15:\n{summary}\n\n"
        "Write a concise 1-3 sentence summary. Mention: the count, asset/submodel types, and concrete "
        "patterns visible in the data (manufacturer, country, value ranges, etc.). Plain text or short markdown."
    )
    content, ms = llm_call(MODEL_SMALL, [{"role": "user", "content": prompt}], max_tokens=300)
    return content.strip(), ms


def _log_event(kind: str, payload: dict) -> None:
    LOG_BUFFER.append({"ts": time.time(), "kind": kind, **payload})


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/log.json")
def log_json():
    return jsonify(list(LOG_BUFFER))


@app.route("/chat", methods=["POST"])
def chat():
    """Agentic chat: the orchestrator decides which read tools to call, then answers."""
    data = request.get_json(force=True)
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400
    try:
        result = orchestrator.run(user_message)
    except Exception as exc:
        log.exception("Orchestrator failed")
        return jsonify({"error": f"Agent failed: {exc}"}), 500
    _log_event("chat", {
        "message": user_message,
        "query_kind": result.get("query_kind"),
        "aasql": result.get("aasql"),
        "query_text": result.get("query_text"),
        "count": result.get("count"),
        "error": result.get("error"),
        "tool_trace": [{"action": t["action"], "args": t["args"]} for t in result.get("tool_trace", [])],
        "timings": result.get("timings"),
    })
    return jsonify(result)


@app.route("/query", methods=["POST"])
def query():
    """Re-run an edited AASQL query directly (no agent). Used by the Run button."""
    data = request.get_json(force=True)
    aasql = data.get("aasql")
    target = data.get("target") or _detect_target(aasql or {})
    user_message = (data.get("message") or "Manual AASQL query").strip()
    explain = bool(data.get("explain", True))
    if not aasql:
        return jsonify({"error": "Missing aasql"}), 400

    timings = {"repo_ms": 0, "llm_explain_ms": 0}
    results, repo_ms, repo_error = _query_repository(aasql, target)
    timings["repo_ms"] = repo_ms
    if explain and not repo_error:
        explanation, exp_ms = _explain_results(user_message, results, target)
        timings["llm_explain_ms"] = exp_ms
    elif repo_error:
        explanation = f"Repository rejected the query: {repo_error}"
    else:
        explanation = ""

    _log_event("query", {
        "message": user_message, "aasql": aasql, "target": target,
        "count": len(results), "repo_error": repo_error, "timings": timings,
    })
    return jsonify({
        "aasql": aasql, "target": target, "results": results, "count": len(results),
        "explanation": explanation, "error": repo_error, "timings": timings,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
