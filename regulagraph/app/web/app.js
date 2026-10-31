// DOM controller. All untrusted text (source quotations, model output, labels) is
// inserted with textContent or SVG text nodes, never as HTML.
import {
  NODE_HALF_HEIGHT,
  NODE_HALF_WIDTH,
  REVIEW_ACTIONS,
  budgetText,
  buildGraph,
  edgePath,
  errorMessage,
  evaluationRows,
  inspectorModel,
  latestReviews,
  liveCallText,
  reviewStatusText,
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

async function api(path, options) {
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
  showInspector(null);
}

// --- scenarios -----------------------------------------------------------------

async function loadScenarios() {
  const list = $("scenario-list");
  list.replaceChildren(el("li", { className: "hint", text: "Loading scenarios…" }));
  try {
    const scenarios = await api("/api/scenarios");
    list.replaceChildren();
    for (const sc of scenarios) {
      const button = el("button", { className: "scenario", attrs: { type: "button", "data-scenario": sc.id } },
        el("span", { className: "scenario-id", text: sc.primary ? `${sc.id} · primary` : sc.id }),
        el("span", { className: "scenario-title", text: sc.title }));
      button.addEventListener("click", () => runReplay(sc, button));
      list.append(el("li", {}, button));
    }
  } catch (err) {
    list.replaceChildren();
    showState(errorMessage(err.status || 0, err.body));
  }
}

let current = null;
let selectedId = null; // relationship shown in the inspector
let reviews = new Map(); // live runs only: relationship_id -> latest review record of `current`
// Latest request wins: each selection gets a new sequence number and aborts the previous request.
// A response or error for a superseded selection is dropped, so it can never overwrite the UI.
let replaySeq = 0;
let replayAbort = null;

async function runReplay(sc, button) {
  const seq = ++replaySeq;
  if (replayAbort) replayAbort.abort();
  replayAbort = new AbortController();
  const { signal } = replayAbort;
  current = null; // stale evidence must not be selectable while the new replay loads
  document.querySelectorAll(".scenario").forEach((b) => b.classList.toggle("selected", b === button));
  clearResult();
  $("question").append(el("span", { className: "label", text: "Question" }), el("p", { text: sc.question }));
  showState({ kind: "loading", title: "Replaying…", body: "Re-verifying the recorded answer against the source text." });
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
  if (replay.relationships.length) selectRelationship(replay.relationships[0].relationship_id);
}

// --- graph -----------------------------------------------------------------------

function renderGraph(replay) {
  const graph = buildGraph(replay);
  const root = $("graph");
  root.replaceChildren();
  root.setAttribute("viewBox", `0 0 ${graph.width} ${graph.height}`);
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
    g.append(svg("text", { x: path.labelX, y: path.labelY, class: "edge-label", "text-anchor": path.textAnchor }, `${e.label} · candidate`));
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
    block.append(caption);
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

async function submitReview(live, relId, decision, note) {
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
  if (current !== live || !record || record.relationship_id !== relId) return; // a newer result is shown
  reviews.set(relId, record);
  if (selectedId === relId) showInspector(inspectorModel(current, relId));
}

async function loadReviews(live) {
  let data;
  try {
    data = await api(`/api/live/investigations/${encodeURIComponent(live.report_id)}/reviews`);
  } catch {
    return; // reviews can still be recorded; existing ones are just not shown
  }
  if (current !== live || !data || data.report_id !== live.report_id) return;
  reviews = latestReviews(data.reviews);
  if (selectedId) showInspector(inspectorModel(current, selectedId));
}

// --- live investigation (local live mode only) -----------------------------------------

const JSON_HEADERS = { Accept: "application/json", "Content-Type": "application/json" };

async function loadLive() {
  let status;
  let docs;
  try {
    status = await api("/api/live/status");
    if (!status || status.enabled !== true) return; // replay-only server: the live panel stays hidden
    docs = await api("/api/live/documents");
  } catch {
    return;
  }
  const banner = $("live-banner");
  banner.textContent = `LIVE MODE ENABLED: "Run live investigation" makes a paid Meta Muse call (${status.model_id}). Curated scenarios remain replays. Every result is an AI-generated candidate, unconfirmed.`;
  banner.hidden = false;
  const box = $("live-docs");
  box.replaceChildren(el("legend", { text: "Source documents (only the selected ones are used)" }));
  for (const d of docs) {
    const input = el("input", { attrs: { type: "checkbox", "data-live-doc": d.document_id } });
    box.append(el("label", { className: "live-doc" }, input, ` ${d.reference || "Circular"} · ${d.title || ""}`));
  }
  $("live-budget").textContent = budgetText(status.budget);
  $("live-run").addEventListener("click", runLive);
  $("live-panel").hidden = false;
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
    showState({ kind: "error", title: "Live investigation not started", body: problem });
    return;
  }
  $("live-confirm").checked = false; // every paid call needs a fresh confirmation
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
    showState(errorMessage(err.status || 0, err.body));
    return;
  }
  if (seq !== replaySeq) return;
  if (!live || live.mode !== "live" || live.question !== question) {
    showState(errorMessage(0, { error: { message: "The live response does not match the request." } }));
    return;
  }
  current = live;
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
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $("view-investigate").hidden = name !== "investigate";
  $("view-evaluation").hidden = name !== "evaluation";
  if (name === "evaluation") loadEvaluation();
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
loadScenarios();
loadLive();
