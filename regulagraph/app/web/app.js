// DOM controller. All untrusted text (source quotations, model output, labels) is
// inserted with textContent or SVG text nodes, never as HTML.
import {
  EXTRACTION_NOTE,
  NODE_HALF_HEIGHT,
  NODE_HALF_WIDTH,
  REVIEW_ACTIONS,
  budgetText,
  buildGraph,
  checksSummary,
  edgeLabelText,
  edgePath,
  errorMessage,
  evaluationRows,
  graphBounds,
  inspectorModel,
  liveCallText,
  mergeReviews,
  modeBanner,
  evaluationSummary,
  primaryRelationship,
  probeRows,
  reviewStatusText,
  scenarioCard,
  snapshotApiPath,
  stateMessage,
} from "./view.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);

function el(tag, { className, text, attrs } = {}, ...children) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}

// Static snapshot (hosted build): marked by a <meta name="regulagraph-snapshot"> tag that only the snapshot
// build adds. The page then reads the exported API responses as static files and never calls a server.
const snapshotMeta = document.querySelector('meta[name="regulagraph-snapshot"]');
const SNAPSHOT = snapshotMeta
  ? { commit: snapshotMeta.getAttribute("data-commit"), builtAt: snapshotMeta.getAttribute("data-built") }
  : null;

async function api(path, options = {}) {
  if (SNAPSHOT) {
    const file = snapshotApiPath(path, options.method || "GET");
    if (!file) {
      throw Object.assign(new Error("not in snapshot"), { status: 404,
        body: { error: { code: "not_in_snapshot", message: "This static snapshot contains only the recorded replays and the evaluation." } } });
    }
    path = file;
    options = { signal: options.signal };
  }
  const response = await fetch(path, { headers: { Accept: "application/json" }, ...options });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) throw Object.assign(new Error("request failed"), { status: response.status, body });
  return body;
}

function showState(message) {
  const box = $("state");
  box.replaceChildren();
  if (!message) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.className = `state ${message.kind}`;
  box.append(el("strong", { text: message.title }), el("p", { text: message.body }));
}

function clearResult() {
  $("question").replaceChildren();
  $("recording").replaceChildren();
  $("relationship-list").replaceChildren();
  $("graph").replaceChildren();
  $("graph-wrap").hidden = true;
  $("rejected").hidden = true;
  $("rejected").replaceChildren();
  selectedId = null;
  reviews = new Map();
  reviewQueues = new Map();
  showInspector(null);
}

// --- scenarios -----------------------------------------------------------------

async function loadScenarios() {
  const list = $("scenario-list");
  list.replaceChildren(el("li", { className: "hint", text: "Loading scenarios…" }));
  try {
    const scenarios = await api("/api/scenarios");
    list.replaceChildren();
    let primary = null;
    for (const sc of scenarios) {
      const card = scenarioCard(sc);
      const button = el("button", { className: "scenario", attrs: { type: "button", "data-scenario": sc.id, title: sc.question } },
        el("span", { className: "scenario-title", text: card.title }),
        el("span", { className: "scenario-id", text: card.meta }));
      button.addEventListener("click", () => runReplay(sc, button));
      list.append(el("li", {}, button));
      if (sc.primary && !primary) primary = { sc, button };
    }
    // First screen: open the primary scenario (a recorded replay, never a model call), unless the
    // user already started something while the list was loading.
    if (primary && replaySeq === 0) runReplay(primary.sc, primary.button);
  } catch (err) {
    list.replaceChildren();
    showState(errorMessage(err.status || 0, err.body));
  }
}

let current = null;
let selectedId = null; // relationship shown in the inspector
let reviews = new Map(); // live runs only: relationship_id -> highest-sequence review record of `current`
let reviewQueues = new Map(); // `${report_id} ${relationship_id}` -> tail of that relationship's submissions
// Latest request wins: each selection gets a new sequence number and aborts the previous request.
// A response or error for a superseded selection is dropped, so it can never overwrite the UI.
let replaySeq = 0;
let liveStatus = null; // /api/live/status when the server has live mode enabled, else null
let modeView = "replay"; // what the investigate view shows: replay | live-loading | live | live-error
let activeView = "investigate";

function renderModeBanner() {
  const view = activeView === "evaluation" ? "replay" : modeView;
  const banner = modeBanner({ liveEnabled: Boolean(liveStatus), view, modelId: liveStatus && liveStatus.model_id, snapshot: SNAPSHOT });
  const box = $("mode-banner");
  box.className = `banner ${banner.kind}`;
  box.textContent = banner.text;
}

function setMode(view) {
  modeView = view;
  renderModeBanner();
}
let replayAbort = null;

async function runReplay(sc, button) {
  const seq = ++replaySeq;
  if (replayAbort) replayAbort.abort();
  replayAbort = new AbortController();
  const { signal } = replayAbort;
  current = null; // stale evidence must not be selectable while the new replay loads
  document.querySelectorAll(".scenario").forEach((b) => b.classList.toggle("selected", b === button));
  clearResult();
  setMode("replay");
  $("question").append(el("span", { className: "label", text: "Question" }), el("p", { text: sc.question }));
  showState({ kind: "loading", title: "Replaying…", body: SNAPSHOT
    ? "Loading the recorded answer (verified when this snapshot was built)."
    : "Re-verifying the recorded answer against the source text." });
  let replay;
  try {
    replay = await api(`/api/scenarios/${encodeURIComponent(sc.id)}/replay`, { method: "POST", signal });
  } catch (err) {
    if (seq !== replaySeq) return;
    showState(errorMessage(err.status || 0, err.body));
    return;
  }
  if (seq !== replaySeq) return;
  if (!replay || !replay.scenario || replay.scenario.id !== sc.id) {
    showState(errorMessage(0, { error: { message: "The replay response does not belong to the selected scenario." } }));
    return;
  }
  current = replay;
  renderReplay(current);
}

function renderReplay(replay) {
  const rec = replay.recording;
  $("recording").replaceChildren(
    el("span", { className: "badge replay", text: "Replay" }),
    el("span", {
      text: ` Recorded ${rec.recorded_at} · ${rec.model_id} · ${rec.prompt_tokens ?? "?"} in / ${rec.completion_tokens ?? "?"} out tokens (${rec.reasoning_tokens ?? "?"} reasoning) · ${rec.file}`,
    }),
  );
  renderBody(replay);
}

// Shared by replay and live results: state, graph, relationship list, rejected proposals.
function renderBody(replay) {
  showState(stateMessage(replay.state));
  if (replay.relationships.length) {
    renderGraph(replay);
    renderList(replay);
  }
  if (replay.rejected.length) {
    const box = $("rejected");
    box.hidden = false;
    box.append(el("strong", { text: `${replay.rejected.length} proposal(s) rejected by validation` }));
    const ul = el("ul");
    for (const r of replay.rejected) ul.append(el("li", { text: `${r.reason}: ${r.detail}` }));
    box.append(ul);
  }
  const first = primaryRelationship(replay);
  if (first) selectRelationship(first.relationship_id);
}

// --- graph -----------------------------------------------------------------------

function renderGraph(replay) {
  const graph = buildGraph(replay);
  const root = $("graph");
  root.replaceChildren();
  // The viewBox is the drawing's own bounding box, so the graph fills the panel. It is never
  // enlarged past 1.3x, so a one-edge graph does not become oversized on wide screens.
  // (Set through CSSOM: the page's CSP, style-src 'self', blocks inline style attributes.)
  const b = graphBounds(graph);
  root.setAttribute("viewBox", `${b.x} ${b.y} ${b.width} ${b.height}`);
  root.style.maxWidth = `${Math.round(b.width * 1.3)}px`;
  const defs = svg("defs");
  const marker = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: "auto-start-reverse" });
  marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "arrowhead" }));
  defs.append(marker);
  root.append(defs);
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));

  for (const e of graph.edges) {
    const from = nodes.get(e.from);
    const to = nodes.get(e.to);
    const path = edgePath(from, to, e.parallelIndex);
    const g = svg("g", { class: "edge", "data-relationship": e.id, tabindex: 0, role: "button" });
    g.append(svg("title", {}, `${e.label} (AI candidate, unconfirmed)`));
    g.append(svg("path", { d: path.d, class: "edge-hit" }));
    g.append(svg("path", { d: path.d, class: "edge-line", "marker-end": "url(#arrow)" }));
    g.append(svg("text", { x: path.labelX, y: path.labelY, class: "edge-label", "text-anchor": path.textAnchor }, edgeLabelText(e)));
    const select = () => selectRelationship(e.id);
    g.addEventListener("click", select);
    g.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        select();
      }
    });
    root.append(g);
  }
  for (const n of graph.nodes) {
    const g = svg("g", { class: `node ${n.kind}` });
    g.append(svg("rect", { x: n.x - NODE_HALF_WIDTH, y: n.y - NODE_HALF_HEIGHT, width: 2 * NODE_HALF_WIDTH,
      height: 2 * NODE_HALF_HEIGHT, rx: 8 }));
    g.append(svg("text", { x: n.x, y: n.y - 4, "text-anchor": "middle", class: "node-label" }, n.label));
    g.append(svg("text", { x: n.x, y: n.y + 15, "text-anchor": "middle", class: "node-sub" }, n.sublabel));
    root.append(g);
  }
  $("graph-wrap").hidden = false;
}

function renderList(replay) {
  const ul = $("relationship-list");
  ul.replaceChildren();
  for (const rel of replay.relationships) {
    const model = inspectorModel(replay, rel.relationship_id);
    const button = el("button", { className: "rel", attrs: { type: "button", "data-relationship": rel.relationship_id } },
      el("span", { className: "badge ai", text: "AI candidate · unconfirmed" }),
      el("span", { text: ` ${model.heading}` }));
    button.addEventListener("click", () => selectRelationship(rel.relationship_id));
    ul.append(el("li", {}, button));
  }
}

function selectRelationship(id) {
  selectedId = id;
  document.querySelectorAll("[data-relationship]").forEach((n) =>
    n.classList.toggle("selected", n.getAttribute("data-relationship") === id));
  showInspector(current ? inspectorModel(current, id) : null);
}

// --- inspector ---------------------------------------------------------------------

function showInspector(model) {
  const box = $("inspector");
  box.replaceChildren(el("h2", { text: "Evidence" }));
  if (!model) {
    box.append(el("p", { className: "hint", text: "Select a relationship." }));
    return;
  }
  const badges = el("div", { className: "badges" });
  for (const b of model.badges) badges.append(el("span", { className: "badge ai", text: b }));
  box.append(badges, el("h3", { className: "rel-heading", text: model.heading }), el("p", { className: "notice", text: model.notice }));
  const dl = el("dl", { className: "facts" });
  dl.append(el("dt", { text: "Source" }), el("dd", { text: model.source }), el("dt", { text: "Relation (proposed)" }),
    el("dd", { text: model.relationType }), el("dt", { text: "Target" }), el("dd", { text: model.target }));
  box.append(dl);

  box.append(el("h4", { text: "Source text (verified location)" }));
  for (const ev of model.evidence) {
    const block = el("figure", { className: `evidence ${ev.verified ? "verified" : "unverified"}` });
    block.append(el("span", { className: `badge ${ev.verified ? "ok" : "warn"}`, text: ev.verifiedLabel }));
    block.append(el("blockquote", { className: "quote", text: ev.quote }));
    const caption = el("figcaption", { text: ev.location });
    if (ev.pdfUrl) {
      caption.append(" · ", el("a", { text: "official BCT PDF", attrs: { href: ev.pdfUrl, target: "_blank", rel: "noopener noreferrer" } }));
    }
    block.append(caption, el("p", { className: "extraction-note", text: EXTRACTION_NOTE }));
    box.append(block);
  }
  box.append(el("p", { className: "hint", text: model.verifiedMeaning }), el("p", { className: "hint", text: model.lexicalNote }));

  box.append(el("h4", { text: "Interpretation (not verified)" }));
  if (model.interpretation) {
    box.append(el("div", { className: "interpretation" }, el("span", { className: "badge warn", text: model.interpretation.label }),
      el("p", { text: model.interpretation.text })));
  } else {
    box.append(el("p", { className: "hint", text: "No interpretation recorded." }));
  }
  if (current && current.mode === "live" && selectedId) box.append(reviewSection(current, selectedId));
}

// --- review (live runs only) ------------------------------------------------------------

function reviewSection(live, relId) {
  const section = el("section", { className: "review" });
  section.append(el("h4", { text: "Your review" }), el("p", { className: "hint", text: live.review_meaning }),
    el("p", { className: "review-status", text: reviewStatusText(reviews.get(relId)) }));
  const note = el("textarea", { attrs: { id: "review-note", rows: 2, maxlength: 1000, "aria-label": "Review note (optional)",
    placeholder: "Note (optional)" } });
  const actions = el("div", { className: "review-actions" });
  for (const [decision, label] of REVIEW_ACTIONS) {
    const button = el("button", { className: "review-btn", text: label, attrs: { type: "button", "data-review": decision } });
    button.addEventListener("click", () => submitReview(live, relId, decision, String(note.value || "")));
    actions.append(button);
  }
  section.append(note, actions, el("p", { className: "review-error", attrs: { id: "review-error" } }));
  return section;
}

// Submissions for one relationship of one run are sent one at a time, in click order, so the server
// appends them in that order. Displayed state only ever moves to a higher server sequence.
function submitReview(live, relId, decision, note) {
  const key = `${live.report_id} ${relId}`;
  const tail = (reviewQueues.get(key) || Promise.resolve()).then(() => sendReview(live, relId, decision, note));
  reviewQueues.set(key, tail.catch(() => {}));
  return tail;
}

function applyReviews(live, records) {
  if (current !== live) return; // another scenario or live run is shown: never apply it there
  reviews = mergeReviews(reviews, records, live.report_id, live.content_digest);
  if (selectedId) showInspector(inspectorModel(current, selectedId));
}

async function sendReview(live, relId, decision, note) {
  if (current !== live) return; // superseded before it was sent
  const reviewsUrl = `/api/live/investigations/${encodeURIComponent(live.report_id)}/reviews`;
  let record;
  try {
    record = await api(reviewsUrl, {
      method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({ relationship_id: relId, content_digest: live.content_digest, decision, note }),
    });
  } catch (err) {
    if (current === live && selectedId === relId) {
      const m = errorMessage(err.status || 0, err.body);
      $("review-error").textContent = `Review not recorded. ${m.title}: ${m.body}`;
    }
    return;
  }
  if (!record || record.relationship_id !== relId) return;
  applyReviews(live, [record]);
  loadReviews(live); // reconcile with the server's authoritative log (merging is order-independent)
}

async function loadReviews(live) {
  let data;
  try {
    data = await api(`/api/live/investigations/${encodeURIComponent(live.report_id)}/reviews`);
  } catch {
    return; // reviews can still be recorded; existing ones are just not shown
  }
  if (!data || data.report_id !== live.report_id || data.content_digest !== live.content_digest) return;
  applyReviews(live, data.reviews);
}

// --- live investigation (local live mode only) -----------------------------------------

const JSON_HEADERS = { Accept: "application/json", "Content-Type": "application/json" };

async function loadLive() {
  if (SNAPSHOT) return; // no server: live mode does not exist in the static snapshot
  let status;
  let docs;
  try {
    status = await api("/api/live/status");
    if (!status || status.enabled !== true) return; // replay-only server: the live panel stays hidden
    docs = await api("/api/live/documents");
  } catch {
    return;
  }
  liveStatus = status;
  renderModeBanner();
  const box = $("live-docs");
  box.replaceChildren(el("legend", { text: "Source documents (only the selected ones are used)" }));
  for (const d of docs) {
    const input = el("input", { attrs: { type: "checkbox", "data-live-doc": d.document_id } });
    box.append(el("label", { className: "live-doc" }, input, ` ${d.reference || "Circular"} · ${d.title || ""}`));
  }
  $("live-budget").textContent = budgetText(status.budget);
  $("live-run").addEventListener("click", runLive);
  const panel = $("live-panel");
  panel.hidden = false;
  // Live-first layout: when this server can make paid calls, the live form comes before the recorded
  // scenarios (moved in the DOM, so keyboard and reading order match the screen). Replay-only servers
  // never reach this point, so their layout is unchanged.
  const picker = panel.parentNode;
  if (picker && picker.firstChild !== panel) {
    picker.prepend(panel);
    picker.classList.add("live-first");
  }
}

function liveRequestProblem(question, documentIds, confirmed) {
  if (!question) return "Enter a question.";
  if (!documentIds.length) return "Select at least one source document.";
  if (!confirmed) return "Confirm the paid Meta call.";
  return null;
}

async function runLive() {
  const question = String($("live-question").value || "").trim();
  const documentIds = Array.from(document.querySelectorAll("[data-live-doc]"))
    .filter((input) => input.checked === true)
    .map((input) => input.getAttribute("data-live-doc"));
  const confirmed = $("live-confirm").checked === true;
  // Same latest-request-wins sequence as scenario replays: a newer selection supersedes this run.
  const seq = ++replaySeq;
  if (replayAbort) replayAbort.abort();
  replayAbort = new AbortController();
  const { signal } = replayAbort;
  current = null;
  document.querySelectorAll(".scenario").forEach((b) => b.classList.toggle("selected", false));
  clearResult();
  $("question").append(el("span", { className: "label", text: "Question (live)" }), el("p", { text: question }));
  const problem = liveRequestProblem(question, documentIds, confirmed);
  if (problem) {
    setMode("replay"); // nothing was sent: no paid call is in progress
    showState({ kind: "error", title: "Live investigation not started", body: `${problem} No paid call was made.` });
    return;
  }
  $("live-confirm").checked = false; // every paid call needs a fresh confirmation
  setMode("live-loading");
  showState({ kind: "loading", title: "Calling Meta Muse (paid)…",
    body: "Only the selected documents are used as source context. Quotations are re-verified against the source text." });
  let live;
  try {
    live = await api("/api/live/investigations", {
      method: "POST", signal, headers: JSON_HEADERS,
      body: JSON.stringify({ question, document_ids: documentIds, confirm_paid_call: true }),
    });
  } catch (err) {
    if (seq !== replaySeq) return;
    setMode("live-error");
    showState(errorMessage(err.status || 0, err.body));
    return;
  }
  if (seq !== replaySeq) return;
  if (!live || live.mode !== "live" || live.question !== question) {
    setMode("live-error");
    showState(errorMessage(0, { error: { message: "The live response does not match the request." } }));
    return;
  }
  current = live;
  setMode("live");
  $("live-budget").textContent = budgetText(live.budget);
  $("recording").replaceChildren(el("span", { className: "badge live", text: "Live" }), el("span", { text: liveCallText(live) }));
  renderBody(live);
  loadReviews(live);
}

// --- evaluation ----------------------------------------------------------------------

async function loadEvaluation() {
  const tbody = $("eval-table").querySelector("tbody");
  tbody.replaceChildren(el("tr", {}, el("td", { text: "Loading…", attrs: { colspan: 5 } })));
  try {
    const data = await api("/api/evaluation");
    $("eval-meaning").textContent = `Scorer version ${data.scoring_version}. ${data.meaning}`;
    const summary = evaluationSummary(data);
    $("eval-summary").replaceChildren(...summary.tiles.map((t) => el("div", { className: `tile ${t.kind || ""}` },
      el("span", { className: "tile-value", text: String(t.value) }), el("span", { className: "tile-label", text: t.label }))));
    const lim = $("eval-limitations");
    lim.replaceChildren(el("strong", { text: "Limitations" }));
    const ul = el("ul");
    for (const text of summary.limitations) ul.append(el("li", { text }));
    lim.append(ul);
    tbody.replaceChildren();
    for (const row of evaluationRows(data)) {
      tbody.append(el("tr", { className: `status-${row.status}` }, el("td", { text: row.id }), el("td", { text: row.phenomenon }),
        el("td", { text: row.polarity }), el("td", {}, el("span", { className: `badge status ${row.status}`, text: row.statusLabel })),
        el("td", { text: row.detail })));
    }
    const s = data.structural;
    const box = $("eval-structural");
    box.replaceChildren();
    if (s) {
      if (s.probe_origin) box.append(el("p", { className: "probe-origin", text: `${s.probe_origin} Recorded run: ${s.source_file}.` }));
      const rows = probeRows(s);
      if (rows.length) {
        const body = el("tbody");
        for (const r of rows) {
          body.append(el("tr", { className: `probe ${r.kind}` }, el("td", { text: r.label }), el("td", { text: r.expected }),
            el("td", {}, el("strong", { text: r.outcome })), el("td", { text: r.reason })));
        }
        box.append(el("div", { className: "eval-table-wrap" }, el("table", { className: "eval-table probe-table" },
          el("thead", {}, el("tr", {}, el("th", { text: "Hand-authored test proposal" }), el("th", { text: "Expected" }),
            el("th", { text: "Outcome" }), el("th", { text: "Reason" }))), body)));
      }
      box.append(el("p", {
        text: `${s.probes} probes: ${s.accept_total} correct proposals accepted; ${s.reject_total} invalid proposals rejected; ${s.undetectable_published} of ${s.undetectable_total} semantically wrong proposals published as candidates.`,
      }), el("p", { className: "hint", text: s.undetectable_meaning }),
      el("p", { text: `Retrieval: gold source provision retrieved in ${s.retrieval_source_hits} of ${s.retrieval_cases} cases; gold target provision in ${s.retrieval_target_hits} of ${s.retrieval_target_total}.` }));
    }
  } catch (err) {
    const m = errorMessage(err.status || 0, err.body);
    tbody.replaceChildren(el("tr", {}, el("td", { text: `${m.title}: ${m.body}`, attrs: { colspan: 5 } })));
  }
}

// --- tabs ---------------------------------------------------------------------------

function showView(name) {
  activeView = name;
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.view === name;
    t.classList.toggle("active", on);
    t.setAttribute("aria-pressed", String(on));
  });
  $("view-investigate").hidden = name !== "investigate";
  $("view-evaluation").hidden = name !== "evaluation";
  renderModeBanner();
  if (name === "evaluation") loadEvaluation();
}

// First screen: one line on what the validators catch and what they cannot, from /api/evaluation only.
async function loadChecks() {
  const box = $("checks-summary");
  try {
    const line = checksSummary((await api("/api/evaluation")).structural);
    if (!line) return;
    const link = el("button", { className: "link", text: "See the evaluation", attrs: { type: "button" } });
    link.addEventListener("click", () => showView("evaluation"));
    box.replaceChildren(el("strong", { text: "Validation test (hand-authored proposals, not Muse output): " }),
      `${line.caught} · `, el("span", { className: "missed", text: line.missed }), " — the reason human review is required. ", link);
    box.hidden = false;
  } catch {
    box.hidden = true; // the line is optional; the evaluation view reports errors itself
  }
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
loadScenarios();
loadLive();
loadChecks();
