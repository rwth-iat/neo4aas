"""chatbot_v2 HTTP server (Flask).

``POST /chat``        — headless run, returns the final answer + tool trace (parity/tests).
``POST /chat/stream`` — Server-Sent Events: emits ``tool_start`` / ``tool_end`` / ``token``
                        / ``done`` so the UI renders tool-call cards and the streamed
                        answer live, Claude-Code style. Memory is per ``thread_id``.
"""

import json
import os
import time
import uuid

from flask import Flask, Response, jsonify, request, send_from_directory
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError

from config import log, get_callbacks
from graph import build_agent
from tools import build_tools

app = Flask(__name__, static_folder="static")

# Hard cap on graph steps (~2 per tool call) so a query that keeps returning nothing can't
# loop until the model context overflows. Observations are size-capped (tools.py), so this
# can be generous; one-shot tools (aggregate_field/property_values) keep most runs short.
_RECURSION_LIMIT = 20
# LangGraph's canned message when the step budget is exhausted (no answer produced).
_CANNED_BUDGET = "need more steps to process"
_BUDGET_MSG = ("I couldn't find a confident answer within my step budget. The data may be "
               "named differently than expected — try rephrasing, or ask me to search a "
               "specific submodel type or property.")

# name -> one-line "what this tool does", shown on each tool card (first docstring line).
_TOOL_DESC = {
    t.name: " ".join((t.description or "").split()).split(". ")[0].rstrip(".")
    for t in build_tools()
}

# Durable per-turn log keyed by thread_id, so a chat id pasted from the UI can be replayed
# for debugging even after the in-memory checkpointer is gone.
TURN_LOG = os.getenv("TURN_LOG", "/tmp/chatbot_v2_turns.jsonl")


def _log_turn(thread_id: str, message: str, answer: str, trace: list, error=None) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "thread_id": thread_id,
           "message": message, "answer": answer, "tool_trace": trace, "error": error}
    try:
        with open(TURN_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — logging must never break a request
        log.warning("turn log write failed: %s", exc)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract(messages: list) -> tuple[str, list]:
    """Pull the final answer and an ordered tool trace out of the agent's messages.

    Pairs each tool call (on an AIMessage) with its ToolMessage result by tool_call_id.
    """
    results_by_id = {
        m.tool_call_id: m.content for m in messages if isinstance(m, ToolMessage)
    }
    trace = []
    final = ""
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in (m.tool_calls or []):
                raw = results_by_id.get(tc["id"], "")
                try:
                    observation = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError):
                    observation = raw
                trace.append({"action": tc["name"], "args": tc["args"],
                              "observation": observation})
            if m.content:
                final = m.content if isinstance(m.content, str) else str(m.content)
    return final, trace


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(app.static_folder, path)


@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    thread_id = body.get("thread_id") or str(uuid.uuid4())

    agent = build_agent()
    try:
        out = agent.invoke(
            {"messages": [{"role": "user", "content": message}]},
            config={"configurable": {"thread_id": thread_id}, "callbacks": get_callbacks(),
                    "recursion_limit": _RECURSION_LIMIT},
        )
    except GraphRecursionError:
        log.warning("recursion limit hit [thread %s]", thread_id)
        _log_turn(thread_id, message, _BUDGET_MSG, [], error="recursion_limit")
        return jsonify({"answer": _BUDGET_MSG, "tool_trace": [], "thread_id": thread_id})
    except Exception as exc:  # noqa: BLE001 — return as JSON error rather than 500 HTML
        log.exception("agent run failed [thread %s]", thread_id)
        _log_turn(thread_id, message, "", [], error=str(exc))
        return jsonify({"error": f"agent failed: {exc}", "thread_id": thread_id}), 200

    final, trace = _extract(out["messages"])
    # Empty final (KIConnect) or the canned budget message → synthesize from observations
    # (the data was usually already fetched; don't throw it away).
    if trace and (not final or _CANNED_BUDGET in final.lower()):
        synth = "".join(_synthesize(agent, thread_id))
        if synth:
            final = synth
    _log_turn(thread_id, message, final, trace)
    return jsonify({"answer": final, "tool_trace": trace, "thread_id": thread_id})


def _coerce(raw):
    """Tool results arrive as JSON strings in ToolMessages; parse for nicer rendering."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    return raw


def _synthesize(agent, thread_id: str):
    """Force a final answer from the run's tool observations (no tools), streamed.

    Used when the agent ends without text (KIConnect occasionally returns empty content).
    """
    from llm import util_model
    try:
        state = agent.get_state({"configurable": {"thread_id": thread_id}})
        msgs = list(state.values.get("messages", []))
    except Exception:  # noqa: BLE001
        return
    msgs.append({"role": "user", "content":
                 "Give your final answer now, concisely, using ONLY the tool results above. "
                 "Do not call any tools."})
    try:
        for ch in util_model().stream(msgs):
            if ch.content:
                yield ch.content
    except Exception as exc:  # noqa: BLE001
        log.warning("synthesize fallback failed: %s", exc)


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    body = request.get_json(force=True) or {}
    message = (body.get("message") or "").strip()
    thread_id = body.get("thread_id") or str(uuid.uuid4())
    if not message:
        return jsonify({"error": "empty message"}), 400

    agent = build_agent()

    def generate():
        yield _sse("start", {"thread_id": thread_id})
        # tool_call_id -> name, so a ToolMessage can be tied back to its call.
        names: dict[str, str] = {}
        trace: list[dict] = []          # accumulated for the durable turn log
        answer_parts: list[str] = []
        err = None
        try:
            for mode, chunk in agent.stream(
                {"messages": [{"role": "user", "content": message}]},
                config={"configurable": {"thread_id": thread_id},
                        "callbacks": get_callbacks(),
                        "recursion_limit": _RECURSION_LIMIT},
                stream_mode=["updates", "messages"],
            ):
                if mode == "updates":
                    for node in chunk.values():
                        for m in node.get("messages", []):
                            if isinstance(m, AIMessage):
                                for tc in (m.tool_calls or []):
                                    names[tc["id"]] = tc["name"]
                                    trace.append({"action": tc["name"], "args": tc["args"]})
                                    yield _sse("tool_start",
                                               {"id": tc["id"], "name": tc["name"],
                                                "args": tc["args"],
                                                "desc": _TOOL_DESC.get(tc["name"], "")})
                            elif isinstance(m, ToolMessage):
                                obs = _coerce(m.content)
                                for t in trace:  # attach observation to its call
                                    if t.get("action") == names.get(m.tool_call_id) and "observation" not in t:
                                        t["observation"] = obs
                                        break
                                yield _sse("tool_end",
                                           {"id": m.tool_call_id,
                                            "name": names.get(m.tool_call_id, "tool"),
                                            "observation": obs})
                elif mode == "messages":
                    msg, meta = chunk
                    # Stream only the final-answer tokens (agent node, no tool_calls).
                    if (isinstance(msg, AIMessageChunk) and msg.content
                            and not msg.tool_call_chunks
                            and meta.get("langgraph_node") == "agent"):
                        answer_parts.append(msg.content)
                        yield _sse("token", {"text": msg.content})
        except GraphRecursionError:
            err = "recursion_limit"
            log.warning("recursion limit hit [thread %s]", thread_id)
            if not answer_parts:
                answer_parts.append(_BUDGET_MSG)
                yield _sse("token", {"text": _BUDGET_MSG})
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            log.exception("stream failed [thread %s]", thread_id)
            yield _sse("error", {"message": err})
        # Salvage: empty final (KIConnect) or the canned budget message → synthesize from
        # the observations (the data was usually already fetched).
        streamed = "".join(answer_parts)
        if not err and trace and (not streamed or _CANNED_BUDGET in streamed.lower()):
            if streamed:  # canned line was shown — separate the real answer from it
                yield _sse("token", {"text": "\n\n"})
            for piece in _synthesize(agent, thread_id):
                answer_parts.append(piece)
                yield _sse("token", {"text": piece})
        _log_turn(thread_id, message, "".join(answer_parts), trace, error=err)
        yield _sse("done", {"thread_id": thread_id, "had_answer": bool(answer_parts)})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/debug/<thread_id>")
def debug(thread_id):
    """Return everything we have for a chat id: live checkpointer state + logged turns.

    Paste the chat id shown in the UI here (``/debug/<id>``) to inspect a conversation —
    the exact messages, tool calls and observations — for debugging.
    """
    out = {"thread_id": thread_id, "turns": [], "messages": []}
    # 1) Durable turn log (survives even after the in-memory checkpointer is gone).
    try:
        with open(TURN_LOG, encoding="utf-8") as fh:
            out["turns"] = [r for line in fh if (r := json.loads(line)).get("thread_id") == thread_id]
    except FileNotFoundError:
        pass
    # 2) Live checkpointer state (full message history) when still in this process.
    try:
        state = build_agent().get_state({"configurable": {"thread_id": thread_id}})
        for m in state.values.get("messages", []):
            out["messages"].append({
                "type": m.__class__.__name__,
                "content": m.content,
                "tool_calls": getattr(m, "tool_calls", None),
                "name": getattr(m, "name", None),
            })
    except Exception as exc:  # noqa: BLE001
        out["state_error"] = str(exc)
    if not out["turns"] and not out["messages"]:
        return jsonify({**out, "note": "unknown chat id (or server restarted)"}), 404
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8091)
