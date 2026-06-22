"""ReAct-style orchestrator: an LLM loop that calls read-only tools and answers.

Each turn the model returns a single JSON object: either a tool call
``{"action": "<name>", "args": {...}}`` or a final answer ``{"final": "<markdown>"}``.
We execute the tool, feed the observation back, and repeat until a final answer or the
step budget is exhausted.
"""

import json
import time

from config import MODEL_LARGE
from llm import llm_call, extract_json
from tools import build_registry, repo_context_text

_MAX_STEPS = 10
_MAX_OBS_CHARS = 6000


def _system_prompt(registry: dict) -> str:
    lines = [
        "You are an assistant for an Asset Administration Shell (AAS) repository (a "
        "laboratory pumping station). You answer the user's question by calling read-only "
        "tools and then explaining the result.",
        "",
    ]
    context = repo_context_text()
    if context:
        lines += [context, ""]
    lines += ["AVAILABLE TOOLS:"]
    for t in registry.values():
        lines.append(f"- {t.name}: {t.description}\n    args: {t.args}")
    lines += [
        "",
        "PROTOCOL — reply with EXACTLY ONE JSON object and nothing else:",
        '  to call a tool:   {"thought": "...", "action": "<tool name>", "args": { ... }}',
        '  to answer:        {"thought": "...", "final": "<concise markdown answer>"}',
        "Every reply MUST contain either an \"action\" or a \"final\" key. Never reply with "
        "thought alone.",
        "",
        "RULES:",
        "- Apart from the REPOSITORY FACTS above, you have NO knowledge of the contents. "
        "NEVER invent or guess ids, idShorts, counts, or values. Every fact in your answer "
        "MUST come from a tool observation in this conversation.",
        "- Any question about repository contents REQUIRES at least one tool call before you "
        "answer. Do not answer such questions from memory.",
        "- SEARCH BROAD FIRST. The data is often not spelled the way the user phrases it, so a "
        "narrow exact-match query wrongly returns nothing. Prefer substring/$contains over "
        "$eq, match a single distinctive token (e.g. 'Endress', not 'Endress+Hauser'), and "
        "avoid stacking many filters. Only narrow down after you have seen results.",
        "- If a tool returns 0 results, the data almost certainly exists but is shaped "
        "differently — DO NOT conclude 'nothing found'. Broaden and retry: drop filters, use "
        "$contains with a shorter token, or use cypher_read with a case-insensitive CONTAINS "
        "over idShort and values (e.g. toLower(n.idShort) CONTAINS 'valve'). Try at least one "
        "broader variant before answering.",
        "- Tool choice: aasql_query for content searches over shells/submodels (find/show/"
        "list devices, assets, submodels by name, value, manufacturer, country, type); "
        "repo_read for fetching/listing specific objects by id (or when the user names the "
        "REST API); cypher_read for counts/aggregations/graph traversals/fuzzy discovery "
        "across the whole graph; count_stats for totals; repo_overview for 'what is in the "
        "repository' overviews.",
        "- aasql_query already has detailed domain knowledge of this repository. For a content "
        "search, call it ONCE passing the user's ORIGINAL question verbatim as 'question' "
        "(do NOT reword, split, or narrow it), then answer from its observation. Only fall "
        "back to another tool if it genuinely returns 0 results after a broadened retry.",
        "- STOP AS SOON AS YOU HAVE AN ANSWER. The moment any tool returns results that answer "
        "the question (count > 0), give the final answer from that observation. Do NOT call "
        "more tools to double-check or re-verify a successful result — that wastes the budget "
        "and risks ending with nothing.",
        "- Ground the final answer strictly in the observations. Be concise.",
        "- If asked what you can do / your capabilities, briefly describe these tools in the "
        "final answer. Do NOT output repository data or invent counts for such meta questions.",
    ]
    return "\n".join(lines)


def _obs_is_empty(obs: dict) -> bool:
    """True when a tool ran successfully but found nothing (so a broader retry is warranted)."""
    if not isinstance(obs, dict) or obs.get("error"):
        return False
    if "results" in obs:
        return not obs["results"]
    if "rows" in obs:
        return not obs["rows"]
    if "count" in obs:
        return obs["count"] == 0
    return False


def _clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= _MAX_OBS_CHARS else s[:_MAX_OBS_CHARS] + " …(truncated)"


def _absorb_results(action: str, args: dict, obs: dict, state: dict) -> None:
    """Track the most recent data-producing observation for the UI workbench.

    Maps every tool's observation onto a common shape the UI renders:
    ``query_kind`` (drives the query-pane label and table mode), ``query_text``
    (the executed query/path, when not AASQL), ``aasql`` and ``results``/``count``.
    """
    if obs.get("error"):
        state["error"] = obs["error"]
        return
    if action == "aasql_query" and "results" in obs:
        state.update(query_kind="aasql", aasql=obs.get("aasql"),
                     target=obs.get("target"), results=obs["results"],
                     count=obs.get("count", len(obs["results"])), query_text=None)
    elif action == "repo_read":
        rows = obs["results"] if "results" in obs else (
            [obs["data"]] if isinstance(obs.get("data"), dict) else obs.get("data", []))
        if isinstance(rows, list):
            state.update(query_kind="repo_read", query_text=obs.get("path"),
                         results=rows, count=obs.get("count", len(rows)), aasql=None)
    elif action == "cypher_read" and "rows" in obs:
        # cypher_read's observation omits the query text; recover it from the args.
        state.update(query_kind="cypher_read", query_text=args.get("cypher"),
                     results=obs["rows"], count=obs.get("count", len(obs["rows"])), aasql=None)
    elif action in ("get_identifiable", "get_referable"):
        state.update(query_kind=action, query_text=None, results=[obs], count=1, aasql=None)
    elif action in ("list_submodel_types", "list_submodel_types_by_semantic_id") and "types" in obs:
        state.update(query_kind=action, query_text=None, results=obs["types"],
                     count=obs.get("total_types", len(obs["types"])), aasql=None)
    elif action == "count_stats":
        state.update(query_kind="count_stats", query_text=None,
                     results=[obs], count=1, aasql=None)
    elif action == "repo_overview":
        state.update(query_kind="repo_overview", query_text=None,
                     results=[obs], count=1, aasql=None)
    elif action == "abstract_submodel" and "abstract_submodel" in obs:
        state.update(query_kind="abstract_submodel", query_text=None,
                     target="submodels", results=[obs["abstract_submodel"]], count=1, aasql=None)
    elif action == "validate_constraints":
        violations = obs.get("violations", [])
        state.update(query_kind="validate_constraints", query_text=None,
                     results=violations, count=len(violations), aasql=None)


def run(user_message: str) -> dict:
    registry = build_registry()
    messages = [
        {"role": "system", "content": _system_prompt(registry)},
        {"role": "user", "content": user_message},
    ]
    timings = {"llm_ms": 0, "tool_ms": 0, "steps": 0}
    trace: list[dict] = []
    state = {"query_kind": None, "query_text": None, "aasql": None, "target": None,
             "results": [], "count": 0, "error": None}
    final = None
    nudges = 0           # pushed back on a no-tool final
    broadens = 0         # pushed back on a final that followed an empty result
    malformed = 0        # replies with neither action nor final
    last_empty = False   # whether the most recent tool call found nothing
    _MAX_NUDGES = 3      # the model is flaky and sometimes finals before acting; insist
    _MAX_BROADEN = 2     # how many times to demand a broader retry after 0 results
    _MAX_MALFORMED = 3   # tolerance for unparseable / action-less replies

    for _ in range(_MAX_STEPS):
        timings["steps"] += 1
        # Generous budget: MODEL_LARGE is a reasoning model and a too-small budget gets
        # consumed by reasoning, leaving empty content and no parseable decision.
        content, ms = llm_call(MODEL_LARGE, messages, max_tokens=2048)
        timings["llm_ms"] += ms

        decision = None
        if content.strip():
            try:
                decision = extract_json(content)
            except ValueError:
                decision = None

        has_action = bool(decision and decision.get("action"))
        has_final = bool(decision and "final" in decision)

        # Malformed turn (empty content, unparseable, or thought-only): re-prompt for the
        # protocol rather than burning the whole budget on bogus "Unknown tool 'None'".
        if not has_action and not has_final:
            malformed += 1
            if malformed > _MAX_MALFORMED:
                final = content.strip() or "I couldn't produce a valid answer."
                break
            messages.append({"role": "assistant", "content": content or "(empty)"})
            messages.append({"role": "user", "content":
                "Your previous reply was not a single valid JSON object with an "
                '"action" or "final" key. Reply now with EXACTLY one JSON object: either '
                '{"action": "<tool>", "args": { ... }} or {"final": "<answer>"}.'})
            continue

        if has_final and not has_action:
            # No tool has run yet: don't let it answer a data question from memory.
            if not trace and nudges < _MAX_NUDGES:
                nudges += 1
                messages.append({"role": "assistant", "content": json.dumps(decision)})
                messages.append({"role": "user", "content":
                    "You have not called any tool yet, so you have NO data. NEVER state "
                    "counts, ids, or values from memory — they would be fabricated. If this "
                    "question is about the repository's contents, you MUST emit a tool call "
                    '({"action": ...}) now. Only answer directly if it is a purely '
                    "general/meta question (e.g. what you can do)."})
                continue
            # Last query found nothing: demand a broader retry before declaring emptiness.
            if last_empty and broadens < _MAX_BROADEN:
                broadens += 1
                messages.append({"role": "assistant", "content": json.dumps(decision)})
                messages.append({"role": "user", "content":
                    "Your last query returned 0 results, but the data very likely exists and "
                    "is just shaped differently than you assumed. Do NOT conclude nothing was "
                    "found. Broaden and try another tool call now: use $contains with a single "
                    "shorter token, drop filters, or use cypher_read with a case-insensitive "
                    "CONTAINS over idShort and values."})
                continue
            final = decision["final"]
            break

        action = decision.get("action")
        args = decision.get("args") or {}
        tool = registry.get(action)
        if tool is None:
            obs = {"error": f"Unknown tool '{action}'. Available: {list(registry)}"}
        else:
            t0 = time.perf_counter()
            try:
                obs = tool.run(args)
            except Exception as exc:
                obs = {"error": f"Tool '{action}' failed: {exc}"}
            timings["tool_ms"] += int((time.perf_counter() - t0) * 1000)
            _absorb_results(action, args, obs, state)
            last_empty = _obs_is_empty(obs)

        trace.append({"thought": decision.get("thought", ""), "action": action,
                      "args": args, "observation": obs})
        messages.append({"role": "assistant", "content": json.dumps(decision)})
        messages.append({"role": "user",
                         "content": f"OBSERVATION from {action}:\n{_clip(obs)}"})

    if final is None:
        # Budget exhausted without an explicit final. If tools did gather data, force one
        # answer from the observations rather than discarding a successful result.
        if trace:
            messages.append({"role": "user", "content":
                "You are out of tool calls. Using ONLY the observations above, give your "
                'final answer now as a single JSON object {"final": "<markdown answer>"}.'})
            content, ms = llm_call(MODEL_LARGE, messages, max_tokens=2048)
            timings["llm_ms"] += ms
            try:
                final = extract_json(content).get("final") or content.strip()
            except ValueError:
                final = content.strip()
        if not final:
            final = "I couldn't complete the request within the step budget."

    return {
        "explanation": final,
        "answer": final,
        "tool_trace": trace,
        "aasql": state["aasql"],
        "target": state["target"],
        "query_kind": state["query_kind"],
        "query_text": state["query_text"],
        "results": state["results"],
        "count": state["count"],
        "error": state["error"],
        "timings": timings,
    }
