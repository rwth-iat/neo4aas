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
from tools import build_registry

_MAX_STEPS = 6
_MAX_OBS_CHARS = 6000


def _system_prompt(registry: dict) -> str:
    lines = [
        "You are an assistant for an Asset Administration Shell (AAS) repository. You answer "
        "the user's question by calling read-only tools and then explaining the result.",
        "",
        "AVAILABLE TOOLS:",
    ]
    for t in registry.values():
        lines.append(f"- {t.name}: {t.description}\n    args: {t.args}")
    lines += [
        "",
        "PROTOCOL — reply with EXACTLY ONE JSON object and nothing else:",
        '  to call a tool:   {"thought": "...", "action": "<tool name>", "args": { ... }}',
        '  to answer:        {"thought": "...", "final": "<concise markdown answer>"}',
        "",
        "RULES:",
        "- You have NO prior knowledge of the repository contents. NEVER invent or guess ids, "
        "idShorts, counts, or values. Every fact in your answer MUST come from a tool observation "
        "in this conversation.",
        "- Any question about what is in the repository REQUIRES at least one tool call before you "
        "answer. Do not produce a final answer from memory.",
        "- Prefer aasql_query for content searches; repo_read for fetching/listing specific objects "
        "(use repo_read when the user mentions the repository REST API or asks to fetch by id); "
        "cypher_read for counts/aggregations/graph traversals/ECLASS discovery.",
        "- Use at most a few tool calls. When you have enough data, give the final answer.",
        "- Ground the final answer strictly in the observations (counts, concrete values). Be concise.",
        "- If asked what you can do / your capabilities, briefly describe these tools in the final "
        "answer. Do NOT output repository data or invent counts for such meta questions.",
    ]
    return "\n".join(lines)


def _clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= _MAX_OBS_CHARS else s[:_MAX_OBS_CHARS] + " …(truncated)"


def _absorb_results(action: str, obs: dict, state: dict) -> None:
    """Track the most recent data-producing observation for the UI workbench."""
    if obs.get("error"):
        state["error"] = obs["error"]
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
        state.update(query_kind="cypher_read", query_text=obs.get("cypher"),
                     results=obs["rows"], count=obs.get("count", len(obs["rows"])), aasql=None)
    elif action in ("get_identifiable", "get_referable") and not obs.get("error"):
        state.update(query_kind=action, query_text=None, results=[obs], count=1, aasql=None)


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
    nudged = False  # whether we already pushed back on a no-tool final

    for _ in range(_MAX_STEPS):
        timings["steps"] += 1
        content, ms = llm_call(MODEL_LARGE, messages)
        timings["llm_ms"] += ms
        try:
            decision = extract_json(content)
        except ValueError:
            # No JSON — treat the raw text as the final answer.
            final = content.strip()
            break

        if "final" in decision and "action" not in decision:
            # Guard against answering data questions from memory: if no tool has run yet,
            # push back once. (A genuinely general/meta question may then answer anyway.)
            if not trace and not nudged:
                nudged = True
                messages.append({"role": "assistant", "content": json.dumps(decision)})
                messages.append({"role": "user", "content":
                    "You have not called any tool yet. If this question is about the "
                    "repository's contents (counts, ids, values, types), you MUST call a "
                    "tool and base your answer on the observation. Only answer directly if "
                    "it is a general/meta question."})
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
            _absorb_results(action, obs, state)

        trace.append({"thought": decision.get("thought", ""), "action": action,
                      "args": args, "observation": obs})
        messages.append({"role": "assistant", "content": json.dumps(decision)})
        messages.append({"role": "user",
                         "content": f"OBSERVATION from {action}:\n{_clip(obs)}"})

    if final is None:
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
