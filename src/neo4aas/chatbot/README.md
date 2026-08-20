# chatbot_v2 — LangGraph AAS agent

LangGraph agent chatbot for the AAS repository (port 8091).

## Features

- **LangGraph `create_react_agent`** with **native tool-calling** (no text-JSON protocol,
  no brace-scraping extractor, no nudge/broaden machinery).
- **Streaming** — `POST /chat/stream` emits SSE `tool_start` / `tool_end` / `token` / `done`.
- **Multi-turn memory** — `MemorySaver` checkpointer keyed by `thread_id`.
- **Claude-Code-style UI** — single-column transcript; every tool call is an inline
  collapsible card (name + args → expandable per-type result); the answer streams below.
- **One tool registry** — in-process LangChain `@tool` wrappers over the shared
  `neo4aas/agent_tools.py`.
- **RAG field discovery** (`find_relevant_fields`) — FAISS index over the repository's real
  field names/semanticIds (`qwen3-embedding-8b`), bridging user vocabulary to actual idShorts.
  **HyDE** (`HYDE=1`, default on): generates hypothetical field names from the original
  question and multi-query-retrieves, so it works even when the question wording is far from
  the real idShorts. The agent passes the original question verbatim.
- **AASQL repair** — generated queries are validated with the local AASQL→Cypher compiler
  and repaired once before execution.
- **Optional Langfuse** tracing (set `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`).
- Read-only.

## Models (KIConnect / RWTHgpt, OpenAI-compatible)

Only 4 models are API-callable on the key (`GET /v1/models`): `gpt-oss-120b`,
`mistralai-mistral-small-4-119b`, `e5-mistral-7b-instruct`, `qwen3-embedding-8b`.
Agent + util → `gpt-oss-120b`; RAG embeddings → `qwen3-embedding-8b`. (The `gpt-5.x` shown
in the KIConnect web UI are **not** API-enabled.)

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/` | UI |
| `POST` | `/chat` | headless run → `{answer, tool_trace, thread_id}` |
| `POST` | `/chat/stream` | SSE stream (`start`/`tool_start`/`tool_end`/`token`/`done`) |
| `GET`  | `/debug/<chat_id>` | full trace for a chat id (logged turns + live message history) |

Every conversation gets a **chat id** (shown + copyable in the UI header). Paste it into
`/debug/<chat_id>` (or send it for debugging) to replay the exact messages, tool calls and
observations. Turns are also appended to `TURN_LOG` (default `/tmp/chatbot_v2_turns.jsonl`),
so a chat is inspectable even after a restart.

## Run

```bash
# from repo root, with the demonstrator stack up (repository on :8081)
export KICONNECT_API_KEY=...           # or source aas_demonstrator/.env
export REPOSITORY_URL=http://localhost:8081/api/v3.1
export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=12345678  # optional: enables neo4aas + RAG tools
uv run --extra chatbot_v2 python -m neo4aas.chatbot
# → http://localhost:8091   (PORT overrides the port)
```

It is a normal package — run it from the repo root, not from inside this directory.

Or via Docker: `docker compose up chatbot-v2` (in `aas_demonstrator/`).

## Config (env)

`KICONNECT_API_KEY` (required), `KICONNECT_BASE_URL`, `REPOSITORY_URL`,
`MODEL_AGENT` / `MODEL_UTIL` / `MODEL_EMBED`, `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`.

## Status

All planned phases done: core agent + native tool-calling, SSE streaming, multi-turn
memory, Claude-Code-style UI, RAG field discovery, AASQL validate→repair, optional Langfuse,
deploy. `spike_kiconnect.py` is the Phase-0 capability probe.
