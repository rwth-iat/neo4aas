"""Phase-0 spike: verify KIConnect supports what the chatbot_v2 design needs.

Probes (against the user's provisioned deployments):
  1. GET /v1/models       — list callable model ids
  2. chat with tools=[...] — native tool-calling returns tool_calls
  3. parallel_tool_calls  — two independent tools in one turn
  4. strict + response_format — structured/strict function args
  5. embeddings           — /v1/embeddings returns a vector

Run:  uv run --with openai python neo4aas/chatbot_v2/spike_kiconnect.py
Reads KICONNECT_API_KEY (+ optional KICONNECT_BASE_URL) from env or aas_demonstrator/.env.
"""

import json
import os
import pathlib

import openai


def _load_env() -> None:
    if os.getenv("KICONNECT_API_KEY"):
        return
    env = pathlib.Path(__file__).resolve().parents[2] / "aas_demonstrator" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
BASE_URL = os.getenv("KICONNECT_BASE_URL", "https://chat.kiconnect.nrw/api/v1")
API_KEY = os.getenv("KICONNECT_API_KEY", "").strip()
CHAT_MODEL = os.getenv("SPIKE_CHAT_MODEL", "gpt-5.3")
EMBED_MODEL = os.getenv("SPIKE_EMBED_MODEL", "qwen3-embedding-8b")

assert API_KEY, "KICONNECT_API_KEY not set (env or aas_demonstrator/.env)"
client = openai.OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=60.0)

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "strict": True,
    },
}
TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current time in a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def _ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


def probe_models() -> None:
    print("1) GET /v1/models")
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        _ok("models listed", bool(ids), f"{len(ids)} models; sample: {ids[:8]}")
    except Exception as exc:  # noqa: BLE001 — spike: report any failure
        _ok("models listed", False, repr(exc))


def probe_tool_calling() -> None:
    print(f"2) tool-calling on {CHAT_MODEL}")
    try:
        r = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": "What's the weather in Aachen? Use the tool."}],
            tools=[WEATHER_TOOL],
            tool_choice="auto",
        )
        tcs = r.choices[0].message.tool_calls or []
        _ok("returned tool_calls", bool(tcs),
            f"{[ (t.function.name, t.function.arguments) for t in tcs ]}")
    except Exception as exc:  # noqa: BLE001
        _ok("returned tool_calls", False, repr(exc))


def probe_parallel() -> None:
    print(f"3) parallel_tool_calls on {CHAT_MODEL}")
    try:
        r = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user",
                       "content": "Get BOTH the weather and the time in Aachen, using both tools."}],
            tools=[WEATHER_TOOL, TIME_TOOL],
            tool_choice="auto",
            parallel_tool_calls=True,
        )
        tcs = r.choices[0].message.tool_calls or []
        _ok("two tool_calls in one turn", len(tcs) >= 2, f"{len(tcs)} calls: {[t.function.name for t in tcs]}")
    except Exception as exc:  # noqa: BLE001
        _ok("two tool_calls in one turn", False, repr(exc))


def probe_structured() -> None:
    print(f"4) response_format json_object on {CHAT_MODEL}")
    try:
        r = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user",
                       "content": 'Reply with a JSON object {"city":"Aachen","country":"DE"} and nothing else.'}],
            response_format={"type": "json_object"},
        )
        content = r.choices[0].message.content or ""
        json.loads(content)
        _ok("valid JSON via response_format", True, content[:80])
    except Exception as exc:  # noqa: BLE001
        _ok("valid JSON via response_format", False, repr(exc))


def probe_embeddings() -> None:
    print(f"5) embeddings on {EMBED_MODEL}")
    try:
        r = client.embeddings.create(model=EMBED_MODEL, input="nominal flow rate of a centrifugal pump")
        vec = r.data[0].embedding
        _ok("embedding vector returned", bool(vec), f"dim={len(vec)}")
    except Exception as exc:  # noqa: BLE001
        _ok("embedding vector returned", False, repr(exc))


if __name__ == "__main__":
    print(f"KIConnect spike — base={BASE_URL} chat={CHAT_MODEL} embed={EMBED_MODEL}\n")
    probe_models()
    probe_tool_calling()
    probe_parallel()
    probe_structured()
    probe_embeddings()
