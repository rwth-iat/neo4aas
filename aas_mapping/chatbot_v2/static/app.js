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
const repoMeta = {};       // repo id → {repository_url, aas_viewer_url} for viewer deep-links

// --- example-query chips, per repository -----------------------------------
const CHIPS = {
  pumpwerk: [
    ["Repository overview", [
      "How many AAS are in the repo?",
      "Give me an overview of the repository.",
      "What submodel types exist?",
    ]],
    ["Search (natural language → AASQL)", [
      "Find all assets made by Krohne.",
      "Give me all properties which contain value 'IP65'",
      "Give me current values in OperationalData Submodel of T23",
    ]],
    ["Aggregation & lookup", [
      "How many devices per manufacturer?",
      "Find an asset with the max flow rate?",
      "Which devices are missing a Nameplate submodel?",
    ]],
    ["Semantic / ECLASS enrichment", [
      "What does the property Max_medium_temperature mean?",
      "Which properties relate to 'flow rate'?",
    ]],
    ["Validation", [
      "Validate AAS constraints on the repo.",
    ]],
  ],
  lieferanten: [
    ["Repository overview", [
      "How many AAS are in the repo?",
      "Which submodel types (by semanticId) exist?",
    ]],
    ["Semantic requirement matching (by IRDI)", [
      "Search for a temperature sensor: measuring range starts at ≤ −20 °C (0173-1#02-AAY818#001), reaches ≥ 110 °C (0173-1#02-AAY819), max. process pressure ≥ 20 bar (0173-1#02-AAY820), max. ambient temperature ≥ 90 °C (0173-1#02-BAA039#010).",
      "Which assets have a max. ambient temperature ≥ 100 °C (0173-1#02-BAA039)?",
    ]]
  ],
};
const HINT_INTRO =
  "Ask about the Asset Administration Shell repository — shells, submodels, manufacturers, " +
  "device properties. Tool calls show inline as they run.";

function renderHint(repoId) {
  const hint = document.getElementById("hint");
  if (!hint) return;
  hint.textContent = HINT_INTRO;
  const groups = CHIPS[repoId] || CHIPS.pumpwerk;
  for (const [label, chips] of groups) {
    hint.appendChild(el("div", "chip-group-label", label));
    const row = el("div", "chips");
    for (const c of chips) {
      const b = el("button", "chip", "");
      b.textContent = c;
      row.appendChild(b);
    }
    hint.appendChild(row);
  }
}

const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// Autoscroll only when the user is already at the bottom — don't yank the view
// while they scroll up to read. Coalesced to one scroll per animation frame so a
// burst of tokens/cards doesn't force a layout reflow on every event.
let atBottom = true;
transcript.addEventListener("scroll", () => {
  atBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
}, { passive: true });
let scrollPending = false;
const scroll = () => {
  if (!atBottom || scrollPending) return;
  scrollPending = true;
  requestAnimationFrame(() => { transcript.scrollTop = transcript.scrollHeight; scrollPending = false; });
};

function addUser(text) {
  const m = el("div", "msg user");
  m.appendChild(el("div", "bubble", esc(text)));
  transcript.appendChild(m);
  scroll();
}

// --- tool card -------------------------------------------------------------
const cards = {};     // tool_call_id -> card element

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
  // Show the Input immediately — don't wait for the result to come back.
  const args = ev.args || {};
  if (Object.keys(args).length) card.querySelector(".tool-body").appendChild(section("Input", pre(args)));
  scroll();
}

function toolEnd(ev) {
  const card = cards[ev.id];
  if (!card) return;
  collectAasIds(ev.observation);   // remember shell ids so the answer can link them
  card.querySelector(".spinner")?.remove();
  const status = card.querySelector(".tool-status");
  const obs = ev.observation;
  const isErr = obs && typeof obs === "object" && obs.error;
  status.className = "tool-status " + (isErr ? "error" : "done");
  status.textContent = isErr ? "error" : (renderCount(obs) ?? "done");

  const body = card.querySelector(".tool-body");
  // Input was already rendered at tool_start.
  // Generated AASQL — the compiled query (aasql_query only).
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

// Parse a CSV string (RFC4180: quoted cells, "" escape) into an array of row objects
// keyed by the header row. Mirrors the csv.DictWriter output of aasql_query's summary.
function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else if (c !== "\r") cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  if (!rows.length) return [];
  const cols = rows.shift();
  return rows.map((r) => Object.fromEntries(cols.map((c, j) => [c, r[j] ?? ""])));
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
  if (obs.format === "csv" && typeof obs.rows === "string") {
    wrap.appendChild(table(parseCsv(obs.rows))); return wrap;
  }
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
let knownAasIds = new Set();   // real AAS shell ids seen in this turn's tool results

// Collect AAS shell ids (aas_id values) from a tool observation, recursively.
// Only these get viewer links — a bare URI in prose is often a semanticId/template id, not a shell.
function collectAasIds(obj) {
  if (!obj || typeof obj !== "object") return;
  if (Array.isArray(obj)) { obj.forEach(collectAasIds); return; }
  for (const [k, v] of Object.entries(obj)) {
    if (k === "aas_id" && typeof v === "string" && v) knownAasIds.add(v);
    else collectAasIds(v);
  }
}
// marked.parse over the whole buffer is O(n) per call; doing it on every token is
// O(n²) and re-lays-out the page each token. Coalesce to one parse per frame.
let renderRaf = 0;
function renderAnswer() {
  renderRaf = 0;
  if (!answerNode) return;
  answerNode.innerHTML = (typeof marked !== "undefined")
    ? marked.parse(answerBuf) : esc(answerBuf).replace(/\n/g, "<br>");
  scroll();
}
function token(text) {
  if (!answerNode) {
    answerNode = el("div", "msg answer");
    transcript.appendChild(answerNode);
    answerBuf = "";
  }
  answerBuf += text;
  if (!renderRaf) renderRaf = requestAnimationFrame(renderAnswer);
}

// base64url-encode an id the way basyx expects (padding kept) — mirrors tools.py _b64url.
const b64url = (s) => btoa(unescape(encodeURIComponent(s)))
  .replace(/\+/g, "-").replace(/\//g, "_");

// Deep-link an AAS id URI into the configured viewer for the current repo.
function viewerLink(uri) {
  const m = repoMeta[currentRepo];
  if (!m || !m.aas_viewer_url || !m.repository_url) return null;
  return `${m.aas_viewer_url}/aasviewer?aas=${m.repository_url}/shells/${b64url(uri)}`;
}

// Once streaming is done, turn AAS shell ids that the tools returned into viewer deep-links,
// then re-render. Done on the assembled buffer so a URI split across tokens is still matched.
// Only ids harvested from tool results are linked (a bare URI is often a semanticId, not a shell).
// Optional wrapping backticks are consumed so the model's `code`-formatted ids become links.
function finalizeAnswer() {
  if (renderRaf) { cancelAnimationFrame(renderRaf); renderRaf = 0; }  // don't let a queued render clobber the linkified HTML
  if (!answerNode) return;
  if (typeof marked === "undefined") { answerNode.innerHTML = esc(answerBuf).replace(/\n/g, "<br>"); return; }
  const linkified = answerBuf.replace(/`?(https?:\/\/[^\s)<>"'`]+)`?/g, (m, raw) => {
    const trail = (raw.match(/[.,;:!?]+$/) || [""])[0];   // keep trailing punctuation outside the link
    const uri = raw.slice(0, raw.length - trail.length);
    if (!knownAasIds.has(uri)) return m;                  // not a known shell id → leave untouched
    const url = viewerLink(uri);
    return url ? `[${uri}](${url})${trail}` : m;
  });
  answerNode.innerHTML = marked.parse(linkified);
  answerNode.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  scroll();
}

// --- SSE streaming POST ----------------------------------------------------
async function send(text) {
  if (busy || !text.trim()) return;
  busy = true; sendBtn.disabled = true;
  document.querySelector(".hint")?.remove();
  atBottom = true;   // user just sent — follow the new output
  addUser(text);
  answerNode = null; answerBuf = ""; knownAasIds = new Set();

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
  else if (ev === "done") finalizeAnswer();
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
  transcript.innerHTML = '<div class="hint" id="hint"></div>';
  renderHint(currentRepo);
});

(async function loadRepos() {
  try {
    const r = await fetch("/repos");
    const d = await r.json();
    for (const repo of d.repos) {
      const opt = document.createElement("option");
      opt.value = repo.id; opt.textContent = repo.label;
      repoSelect.appendChild(opt);
      repoMeta[repo.id] = { repository_url: repo.repository_url, aas_viewer_url: repo.aas_viewer_url };
    }
    currentRepo = d.default;
    repoSelect.value = d.default;
    renderHint(currentRepo);
  } catch (err) { console.error("failed to load repos", err); renderHint("pumpwerk"); }
})();
