"use strict";

const transcript = document.getElementById("transcript");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");
const repoSelect = document.getElementById("repo");

let threadId = null;       // persists across turns → multi-turn memory
let currentRepo = null;    // selected repository id (sent with each turn)
let busy = false;

const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const scroll = () => { transcript.scrollTop = transcript.scrollHeight; };

function addUser(text) {
  const m = el("div", "msg user");
  m.appendChild(el("div", "bubble", esc(text)));
  transcript.appendChild(m);
  scroll();
}

// --- tool card -------------------------------------------------------------
const cards = {};     // tool_call_id -> card element
const cardArgs = {};  // tool_call_id -> input args

function argSummary(args) {
  if (!args || !Object.keys(args).length) return "";
  const first = Object.values(args).find((v) => typeof v === "string" && v.length);
  if (first) return first.length > 90 ? first.slice(0, 90) + "…" : first;
  return JSON.stringify(args).slice(0, 90);
}

function section(title, node) {
  const s = el("div", "section");
  s.appendChild(el("div", "section-title", esc(title)));
  s.appendChild(node);
  return s;
}

function toolStart(ev) {
  const card = el("div", "tool");
  card.innerHTML = `
    <div class="tool-head">
      <span class="caret">▸</span>
      <span class="tool-icon">⚒</span>
      <span class="tool-name">${esc(ev.name)}</span>
      <span class="tool-args">${esc(argSummary(ev.args))}</span>
      <span class="spinner"></span>
      <span class="tool-status running"></span>
    </div>
    ${ev.desc ? `<div class="tool-desc">${esc(ev.desc)}</div>` : ""}
    <div class="tool-body"></div>`;
  card.querySelector(".tool-head").addEventListener("click", () => card.classList.toggle("open"));
  transcript.appendChild(card);
  cards[ev.id] = card;
  cardArgs[ev.id] = ev.args || {};
  scroll();
}

function toolEnd(ev) {
  const card = cards[ev.id];
  if (!card) return;
  card.querySelector(".spinner")?.remove();
  const status = card.querySelector(".tool-status");
  const obs = ev.observation;
  const isErr = obs && typeof obs === "object" && obs.error;
  status.className = "tool-status " + (isErr ? "error" : "done");
  status.textContent = isErr ? "error" : (renderCount(obs) ?? "done");

  const body = card.querySelector(".tool-body");
  const args = cardArgs[ev.id];
  // 1) Input — what the agent passed to the tool.
  if (args && Object.keys(args).length) body.appendChild(section("Input", pre(args)));
  // 2) Generated AASQL — the compiled query (aasql_query only).
  if (obs && typeof obs === "object" && obs.aasql) {
    body.appendChild(section("Generated AASQL  →  " + (obs.target || ""), pre(obs.aasql)));
  }
  // 3) Result / output.
  body.appendChild(section(isErr ? "Error" : "Result", renderObs(obs)));

  if (isErr) card.classList.add("open");
  scroll();
}

function renderCount(obs) {
  if (obs && typeof obs === "object") {
    if (typeof obs.count === "number") return obs.count + " result" + (obs.count === 1 ? "" : "s");
    if (Array.isArray(obs.results)) return obs.results.length + " results";
    if (Array.isArray(obs.rows)) return obs.rows.length + " rows";
    if (Array.isArray(obs.types)) return obs.types.length + " types";
  }
  return null;
}

function table(rows) {
  if (!rows.length) return el("div", "meta", "empty");
  const cols = [...rows.reduce((s, r) => { Object.keys(r || {}).forEach((k) => s.add(k)); return s; }, new Set())];
  const cell = (v) => v == null ? "" : (typeof v === "object" ? JSON.stringify(v) : String(v));
  const head = `<tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const body = rows.map((r) => `<tr>${cols.map((c) => `<td title="${esc(cell(r[c]))}">${esc(cell(r[c]))}</td>`).join("")}</tr>`).join("");
  const wrap = el("div", "scroll");
  wrap.appendChild(el("table", null, head + body));
  return wrap;
}

function kv(obj) {
  const node = el("div", "kv");
  Object.entries(obj).forEach(([k, v]) => {
    node.appendChild(el("b", null, esc(k)));
    node.appendChild(el("span", null, esc(typeof v === "object" ? JSON.stringify(v) : v)));
  });
  return node;
}

function pre(obj) {
  return el("pre", null, esc(JSON.stringify(obj, null, 2)));
}

// per-tool-type result rendering, inside the card
function renderObs(obs) {
  if (obs == null) return el("div", "meta", "no output");
  if (typeof obs !== "object") return el("pre", null, esc(String(obs)));
  if (obs.error) return el("div", "err", esc(obs.error));
  const wrap = el("div");
  if (Array.isArray(obs.results)) { wrap.appendChild(table(obs.results)); return wrap; }
  if (Array.isArray(obs.rows)) { wrap.appendChild(table(obs.rows)); return wrap; }
  if (Array.isArray(obs.types)) { wrap.appendChild(table(obs.types)); return wrap; }
  if (Array.isArray(obs.violations)) { wrap.appendChild(table(obs.violations)); return wrap; }
  // single object fetch / stats → kv for shallow, pre for nested
  const shallow = Object.values(obs).every((v) => typeof v !== "object" || v === null);
  wrap.appendChild(shallow ? kv(obs) : pre(obs));
  return wrap;
}

// --- answer block ----------------------------------------------------------
let answerBuf = "";
let answerNode = null;
function token(text) {
  if (!answerNode) {
    answerNode = el("div", "msg answer");
    transcript.appendChild(answerNode);
    answerBuf = "";
  }
  answerBuf += text;
  answerNode.innerHTML = (typeof marked !== "undefined")
    ? marked.parse(answerBuf) : esc(answerBuf).replace(/\n/g, "<br>");
  scroll();
}

// --- SSE streaming POST ----------------------------------------------------
async function send(text) {
  if (busy || !text.trim()) return;
  busy = true; sendBtn.disabled = true;
  document.querySelector(".hint")?.remove();
  addUser(text);
  answerNode = null; answerBuf = "";

  const resp = await fetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text, thread_id: threadId, repo: currentRepo }),
  });

  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const blocks = buf.split("\n\n");
    buf = blocks.pop();  // keep incomplete tail
    for (const block of blocks) {
      try { handleEvent(block); }      // one bad event must not kill the stream
      catch (err) { console.error("event render failed", err, block); }
    }
  }
  busy = false; sendBtn.disabled = false; input.focus();
}

function handleEvent(block) {
  let ev = "message", data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) ev = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let d; try { d = JSON.parse(data); } catch { return; }
  if (ev === "start") { threadId = d.thread_id; showChatId(d.thread_id); }
  else if (ev === "tool_start") toolStart(d);
  else if (ev === "tool_end") toolEnd(d);
  else if (ev === "token") token(d.text);
  else if (ev === "error") token("\n\n**Error:** " + d.message);
}

// --- chat id (copyable, for debugging) -------------------------------------
const chatIdBox = document.getElementById("chatid");
const chatIdVal = document.getElementById("chatid-val");
function showChatId(id) {
  if (!id) return;
  chatIdVal.textContent = id;
  chatIdBox.hidden = false;
}
document.getElementById("chatid-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(chatIdVal.textContent); } catch {}
  const b = document.getElementById("chatid-copy");
  const prev = b.textContent; b.textContent = "✓";
  setTimeout(() => { b.textContent = prev; }, 1000);
});

form.addEventListener("submit", (e) => { e.preventDefault(); const t = input.value; input.value = ""; send(t); });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});
input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = input.scrollHeight + "px"; });
transcript.addEventListener("click", (e) => {
  if (e.target.classList.contains("chip")) send(e.target.textContent);
});
resetBtn.addEventListener("click", () => { threadId = null; transcript.innerHTML = ""; location.reload(); });

// --- repository selector ---------------------------------------------------
// Switching repos starts a fresh conversation (each repo has its own agent/memory).
repoSelect.addEventListener("change", () => {
  currentRepo = repoSelect.value;
  threadId = null;
  chatIdBox.hidden = true;
  transcript.innerHTML = "";
});

(async function loadRepos() {
  try {
    const r = await fetch("/repos");
    const d = await r.json();
    for (const repo of d.repos) {
      const opt = document.createElement("option");
      opt.value = repo.id; opt.textContent = repo.label;
      repoSelect.appendChild(opt);
    }
    currentRepo = d.default;
    repoSelect.value = d.default;
  } catch (err) { console.error("failed to load repos", err); }
})();
