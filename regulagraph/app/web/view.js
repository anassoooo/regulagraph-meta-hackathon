// Pure view logic: no DOM access, so it can be unit-tested with `node --test`.
// Every value shown to the user comes from the API response; nothing here
// hardcodes evidence, pages or offsets.

export const OFFICIAL_PDF_PREFIX = "https://www.bct.gov.tn/";

export const STATE_MESSAGES = {
  ok: null,
  empty: {
    kind: "empty",
    title: "No relationship proposed",
    body: "The recorded model answer proposed no relationship for this question. Nothing was published.",
  },
  truncated: {
    kind: "warning",
    title: "Truncated: no answer",
    body: "The model ran out of its token budget before answering. A partial answer is never parsed, so nothing was published.",
  },
  refused: { kind: "warning", title: "Model refused", body: "The model declined to answer. Nothing was published." },
  content_filter: {
    kind: "warning",
    title: "Blocked by content filter",
    body: "The provider filtered the answer. Nothing was published.",
  },
  malformed: {
    kind: "warning",
    title: "Malformed answer",
    body: "The model's answer did not match the required JSON format. Nothing was published.",
  },
  provider_error: { kind: "error", title: "Provider error", body: "The model provider returned an error. Nothing was published." },
  disabled: { kind: "error", title: "Inference disabled", body: "No model call was allowed. Nothing was published." },
  invalid_request: { kind: "error", title: "Invalid request", body: "The request was rejected before sending. Nothing was published." },
};

export function stateMessage(state) {
  if (!(state in STATE_MESSAGES)) {
    return { kind: "error", title: "Unknown state", body: `Unexpected result state: ${String(state)}.` };
  }
  return STATE_MESSAGES[state];
}

export function errorMessage(status, body) {
  const err = body && body.error ? body.error : null;
  if (status === 503) {
    return {
      kind: "error",
      title: "Demo data unavailable",
      body: (err && err.message) || "The corpus or recordings are missing or failed verification.",
    };
  }
  if (status === 404) return { kind: "error", title: "Scenario not found", body: (err && err.message) || "Unknown scenario." };
  return { kind: "error", title: "Request failed", body: (err && err.message) || `HTTP ${status}` };
}

export const RELATION_LABELS = {
  amends: "amends",
  repeals: "repeals",
  supersedes: "supersedes",
  references: "references",
  implements: "implements",
  conflicts_with: "conflicts with",
};

export function relationLabel(type) {
  return RELATION_LABELS[type] || String(type);
}

export function safePdfUrl(url) {
  return typeof url === "string" && url.startsWith(OFFICIAL_PDF_PREFIX) ? url : null;
}

function index(list, key) {
  const out = new Map();
  for (const item of list || []) out.set(item[key], item);
  return out;
}

export function documentLabel(doc) {
  return (doc && (doc.reference || doc.document_id)) || "unknown document";
}

export function provisionLabel(prov, docs) {
  if (!prov) return "unknown provision";
  const doc = docs.get(prov.document_id);
  return `${documentLabel(doc)}, ${prov.label}`;
}

// Graph: one node per involved provision, plus one node per whole-document target.
// Columns follow document order in the response (the source document first);
// rows follow page order. Parallel edges (same endpoints) get distinct curve offsets.
export function buildGraph(replay, { width = 860, columnGap = 400, top = 130, rowGap = 110 } = {}) {
  const docs = index(replay.documents, "document_id");
  const provs = index(replay.provisions, "provision_id");
  const docOrder = (replay.documents || []).map((d) => d.document_id);
  const nodes = new Map();

  function addProvisionNode(id) {
    if (nodes.has(id)) return;
    const p = provs.get(id);
    nodes.set(id, {
      id,
      kind: "provision",
      documentId: p ? p.document_id : null,
      label: p ? p.label : id,
      sublabel: documentLabel(p ? docs.get(p.document_id) : null),
      page: p ? p.page_start : 0,
    });
  }

  for (const rel of replay.relationships || []) {
    addProvisionNode(rel.source_provision_id);
    if (rel.target_kind === "provision") {
      addProvisionNode(rel.target_id);
    } else if (!nodes.has(rel.target_id)) {
      nodes.set(rel.target_id, {
        id: rel.target_id,
        kind: "document",
        documentId: rel.target_id,
        label: documentLabel(docs.get(rel.target_id)),
        sublabel: "whole circular",
        page: 0,
      });
    }
  }

  // Columns: a document's provisions share a column; whole-document targets sit right of everything.
  const columnOf = (n) => {
    const i = docOrder.indexOf(n.documentId);
    return n.kind === "document" ? docOrder.length + Math.max(i, 0) : Math.max(i, 0);
  };
  const usedColumns = [...new Set([...nodes.values()].map(columnOf))].sort((a, b) => a - b);
  const columnIndex = new Map(usedColumns.map((c, i) => [c, i]));
  const byColumn = new Map();
  for (const n of [...nodes.values()].sort((a, b) => a.page - b.page || a.id.localeCompare(b.id))) {
    const c = columnIndex.get(columnOf(n));
    if (!byColumn.has(c)) byColumn.set(c, []);
    byColumn.get(c).push(n);
  }
  const columns = usedColumns.length;
  const left = columns === 1 ? width / 2 : Math.max(120, (width - columnGap * (columns - 1)) / 2);
  for (const [c, list] of byColumn) {
    list.forEach((n, row) => {
      n.x = columns === 1 ? left : left + c * columnGap;
      n.y = top + row * rowGap;
    });
  }
  // Single-column graphs (internal references) are laid out vertically with the edge curving to the right.

  const pairCount = new Map();
  const edges = (replay.relationships || []).map((rel) => {
    const key = `${rel.source_provision_id}->${rel.target_id}`;
    const k = pairCount.get(key) || 0;
    pairCount.set(key, k + 1);
    return {
      id: rel.relationship_id,
      from: rel.source_provision_id,
      to: rel.target_id,
      label: relationLabel(rel.relation_type),
      parallelIndex: k,
    };
  });
  const height = top + rowGap * Math.max(1, ...[...byColumn.values()].map((l) => l.length)) - rowGap / 2;
  return { nodes: [...nodes.values()], edges, width, height };
}

export const NODE_HALF_WIDTH = 115;
export const NODE_HALF_HEIGHT = 28;
export const EDGE_LABEL_FONT = 15; // px in graph units; keep in sync with .edge-label in style.css

// Quadratic curve between the borders of two node boxes (so the arrowhead is not hidden
// under the target box); parallel edges bend progressively further. A quadratic curve's apex
// is half its bend away from the chord, so an 80px bend step puts apexes 40px apart: enough for
// each 14px label (drawn just outside its own apex) to clear the neighbouring curve and label.
export const PARALLEL_BEND_STEP = 80;

export function edgePath(from, to, parallelIndex = 0) {
  const bend = 40 + PARALLEL_BEND_STEP * parallelIndex;
  if (from.x === to.x) {
    // Same column: leave from the right side of the source box and enter the right side of the target box.
    const x0 = from.x + NODE_HALF_WIDTH;
    const x1 = to.x + NODE_HALF_WIDTH + 6;
    const cx = from.x + NODE_HALF_WIDTH + 40 + bend;
    const cy = (from.y + to.y) / 2;
    return { d: `M ${x0} ${from.y} Q ${cx} ${cy} ${x1} ${to.y}`, labelX: cx - 10, labelY: cy, textAnchor: "start",
      apexX: (x0 + 2 * cx + x1) / 4, apexY: (from.y + 2 * cy + to.y) / 4 };
  }
  // Different columns: parallel edges alternate sides (even index arcs above the boxes, odd below),
  // each label outside its own arc. A label is wider than the short gap between the boxes, so two
  // arcs on the same side would cross each other's labels; opposite sides never do.
  const dir = to.x > from.x ? 1 : -1;
  const side = parallelIndex % 2 === 0 ? -1 : 1; // -1: above, 1: below
  const arc = 40 + PARALLEL_BEND_STEP * Math.floor(parallelIndex / 2);
  const x0 = from.x + dir * NODE_HALF_WIDTH;
  const x1 = to.x - dir * (NODE_HALF_WIDTH + 6);
  const y0 = from.y + (side * NODE_HALF_HEIGHT) / 2;
  const y1 = to.y + (side * NODE_HALF_HEIGHT) / 2;
  const mx = (x0 + x1) / 2;
  const my = (y0 + y1) / 2 + side * arc;
  const labelY = side < 0 ? (y0 + y1) / 2 - arc / 2 - 6 : (y0 + y1) / 2 + arc / 2 + EDGE_LABEL_FONT + 2;
  return { d: `M ${x0} ${y0} Q ${mx} ${my} ${x1} ${y1}`, labelX: mx, labelY, textAnchor: "middle",
    apexX: (x0 + 2 * mx + x1) / 4, apexY: (y0 + 2 * my + y1) / 4 };
}

// The text drawn on an edge: relation type plus its candidate status (never dropped).
export function edgeLabelText(edge) {
  return `${edge.label} · candidate`;
}

// Approximate box of an edge label (graph units). 0.62em per character over-estimates the
// average width of the UI font, so the box errs on the large side.
export function edgeLabelBox(path, edge) {
  const width = edgeLabelText(edge).length * EDGE_LABEL_FONT * 0.62;
  const left = path.textAnchor === "start" ? path.labelX : path.labelX - width / 2;
  return { left, right: left + width, top: path.labelY - EDGE_LABEL_FONT, bottom: path.labelY + 4 };
}

// Tight bounding box of everything drawn (node boxes, curve apexes, edge labels), plus padding,
// for the SVG viewBox: the graph then fills its panel instead of a fixed 860-unit canvas.
export function graphBounds(graph, pad = 18) {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  const add = (x, y) => {
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x);
    y1 = Math.max(y1, y);
  };
  for (const n of graph.nodes) {
    add(n.x - NODE_HALF_WIDTH, n.y - NODE_HALF_HEIGHT);
    add(n.x + NODE_HALF_WIDTH, n.y + NODE_HALF_HEIGHT);
  }
  for (const e of graph.edges) {
    const path = edgePath(nodes.get(e.from), nodes.get(e.to), e.parallelIndex);
    add(path.apexX, path.apexY);
    const box = edgeLabelBox(path, e);
    add(box.left, box.top);
    add(box.right, box.bottom);
  }
  if (!graph.nodes.length) return { x: 0, y: 0, width: graph.width, height: graph.height };
  return { x: x0 - pad, y: y0 - pad, width: x1 - x0 + 2 * pad, height: y1 - y0 + 2 * pad };
}

// The relationship to open first: verified evidence first, then a quotation that names the
// target, then more evidence spans; ties keep the response order. Purely a display choice.
export function primaryRelationship(replay) {
  const rels = replay.relationships || [];
  const score = (r) => [
    (r.evidence || []).some((e) => e.source_text_verified === true) ? 1 : 0,
    r.target_mentioned_in_quote === true ? 1 : 0,
    (r.evidence || []).length,
  ];
  const better = (a, b) => {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] > b[i];
    return false; // equal: keep the earlier one
  };
  let best = null;
  let bestScore = null;
  for (const r of rels) {
    const s = score(r);
    if (!best || better(s, bestScore)) {
      best = r;
      bestScore = s;
    }
  }
  return best;
}

// --- mode indicator ------------------------------------------------------------------------

// One banner for the whole page. `view` is what the user is looking at: "replay" (recorded
// results, including the evaluation), "live-loading", "live" (a live result) or "live-error".
// When the server can make paid calls the banner never says that no model is called.
export function modeBanner({ liveEnabled = false, view = "replay", modelId = "Meta Muse", snapshot = null } = {}) {
  if (snapshot) {
    // Static snapshot: there is no server, so nothing was re-verified on this visit and no model can be called.
    return {
      kind: "snapshot",
      text: `STATIC SNAPSHOT · Recorded Muse answers exported from commit ${snapshot.commit || "unknown"} on ${snapshot.builtAt || "unknown date"}. Quotations were verified against the official BCT PDF text when this snapshot was built, not re-verified in your browser. No model and no server are called.`,
    };
  }
  if (!liveEnabled) {
    return { kind: "replay", text: "REPLAY MODE · Recorded Muse answers, re-verified against the official PDF text. No model is called." };
  }
  if (view === "live-loading") return { kind: "live", text: `LIVE · Paid Meta Muse call in progress (${modelId}).` };
  if (view === "live") {
    return { kind: "live", text: `LIVE RESULT · From a paid Meta Muse call (${modelId}). Every relationship is an AI-generated candidate, unconfirmed.` };
  }
  if (view === "live-error") return { kind: "live", text: "LIVE · The paid live request did not produce a result. Nothing was published." };
  return {
    kind: "replay-live",
    text: "LIVE MODE ON · Curated scenarios and the evaluation are recorded replays, not new calls. Only “Run live investigation” makes a paid Meta Muse call.",
  };
}

export function inspectorModel(replay, relationshipId) {
  const rel = (replay.relationships || []).find((r) => r.relationship_id === relationshipId);
  if (!rel) return null;
  const docs = index(replay.documents, "document_id");
  const provs = index(replay.provisions, "provision_id");
  const target =
    rel.target_kind === "provision"
      ? { label: provisionLabel(provs.get(rel.target_id), docs), kind: "provision" }
      : { label: `${documentLabel(docs.get(rel.target_id))} (whole circular)`, kind: "document" };
  return {
    heading: `${provisionLabel(provs.get(rel.source_provision_id), docs)} ${relationLabel(rel.relation_type)} ${target.label}`,
    badges: ["AI-generated", "Candidate", "Unconfirmed"],
    notice: rel.notice,
    relationType: relationLabel(rel.relation_type),
    source: provisionLabel(provs.get(rel.source_provision_id), docs),
    target: target.label,
    evidence: rel.evidence.map((e) => ({
      quote: e.quote,
      location: `${documentLabel(docs.get(e.document_id))}, page ${e.page_number}, characters ${e.char_start}–${e.char_end}`,
      verified: e.source_text_verified === true,
      verifiedLabel: e.source_text_verified === true ? "Source text verified (exact location)" : "Source text NOT verified",
      pdfUrl: safePdfUrl(e.pdf_url),
    })),
    verifiedMeaning: replay.verified_meaning,
    lexicalNote: rel.target_mentioned_in_quote
      ? "The quotation names the target. This is a word match, not proof of the relationship."
      : "The quotation does not name the target by number.",
    interpretation: rel.interpretation
      ? { text: rel.interpretation.text, label: `Model interpretation (${rel.interpretation.model_id}): not verified` }
      : null,
  };
}

// --- scenario cards (P1) ------------------------------------------------------------------------

// Plain-language English renderings of each curated scenario's own French question. They restate
// the question only (no answer, no relationship claim). The API title stays as secondary text.
export const SCENARIO_PLAIN_TITLES = {
  E01: "Which circular does circular 2026-10 repeal or replace?",
  E12: "Under which article of circular 2026-10 is Article 47 adopted?",
  E05: "How is Article 15 of circular 2026-10 linked to Article 14?",
  E13: "Does Article 35 of circular 2026-10 repeal or contradict Article 17?",
};

export function scenarioCard(sc) {
  const plain = SCENARIO_PLAIN_TITLES[sc.id];
  return {
    title: plain || sc.title,
    meta: plain ? `${sc.id}${sc.primary ? " · primary" : ""} · ${sc.title}` : `${sc.id}${sc.primary ? " · primary" : ""}`,
  };
}

// --- evaluation overview (P1) -------------------------------------------------------------------

// Counts computed from /api/evaluation only; nothing is assumed.
export function evaluationSummary(evaluation) {
  const cases = evaluation.cases || [];
  const count = (status) => cases.filter((c) => c.status === status).length;
  const notRun = count("not_run");
  const s = evaluation.structural;
  const limitations = [
    `${notRun} of ${cases.length} cases have not been run live; only recorded runs are scored.`,
    "“Validated” means the answer matched the author's annotation. It is not a legal verification, and the annotations have not been reviewed by a domain expert.",
  ];
  if (count("truncated")) limitations.push(`${count("truncated")} live run(s) were truncated by the token budget and produced no answer.`);
  if (s) {
    limitations.push(`Structural checks cannot catch semantic errors: ${s.undetectable_published} of ${s.undetectable_total} deliberately wrong proposals with real quotations were still published as candidates. This is why human review exists.`);
  }
  return {
    tiles: [
      { label: "Cases", value: cases.length },
      { label: "Run live", value: cases.length - notRun },
      { label: "Validated", value: count("validated"), kind: "ok" },
      { label: "Truncated", value: count("truncated"), kind: "warn" },
      { label: "Not validated", value: count("not_validated"), kind: "warn" },
      { label: "Not run live", value: notRun, kind: "muted" },
    ],
    limitations,
  };
}

export const EVAL_STATUS_LABELS = {
  validated: "Validated (annotation matched)",
  truncated: "Truncated: no answer",
  not_validated: "Not validated",
  not_run: "Not run live",
};

export function evaluationRows(evaluation) {
  return (evaluation.cases || []).map((c) => ({
    id: c.id,
    phenomenon: c.phenomenon.replaceAll("_", " "),
    polarity: c.polarity,
    status: c.status,
    statusLabel: EVAL_STATUS_LABELS[c.status] || c.status,
    detail:
      c.status === "truncated"
        ? `finish_reason=${c.finish_reason}; ${c.reasoning_tokens} of ${c.completion_tokens} completion tokens were reasoning`
        : (c.errors || []).join(", "),
  }));
}

// --- live review (Sprint 3.5) ------------------------------------------------------------

export const REVIEW_ACTIONS = [
  ["accepted", "Accept (reviewer)"],
  ["rejected", "Reject"],
  ["needs_more_evidence", "Needs more evidence"],
];

export const REVIEW_LABELS = {
  accepted: "Reviewer accepted",
  rejected: "Reviewer rejected",
  needs_more_evidence: "Reviewer: needs more evidence",
};

// Merge review records into `known` (relationship_id -> record), keeping per relationship the record
// with the highest server `sequence` (its position in the server's review log). The order
// in which responses arrive therefore never matters: a stale read or a late response cannot replace a
// newer decision. Records for another run, or without a valid sequence, are ignored.
export function mergeReviews(known, records, reportId, contentDigest) {
  const merged = new Map(known || []);
  for (const r of records || []) {
    if (!r || r.report_id !== reportId || r.content_digest !== contentDigest) continue;
    if (!Number.isSafeInteger(r.sequence) || r.sequence < 1 || typeof r.relationship_id !== "string") continue;
    const have = merged.get(r.relationship_id);
    if (!have || r.sequence > have.sequence) merged.set(r.relationship_id, r);
  }
  return merged;
}

export function reviewStatusText(record) {
  if (!record) return "No review recorded.";
  const label = REVIEW_LABELS[record.decision] || "Unknown decision";
  return `${label} · ${record.reviewed_at}. Still an AI-generated candidate: not a legal confirmation.`;
}

export function liveCallText(live) {
  const c = live.call || {};
  const cost = typeof c.estimated_cost_usd === "number" ? `~${c.estimated_cost_usd.toFixed(4)} USD (estimated)` : "cost unknown";
  const docs = (live.approved_document_ids || []).length;
  return ` ${live.created_at} · ${c.model_id} · ${c.prompt_tokens ?? "?"} in / ${c.completion_tokens ?? "?"} out tokens (${c.reasoning_tokens ?? "?"} reasoning) · ${cost} · ${docs} approved source document(s)`;
}

export function budgetText(budget) {
  if (!budget) return "";
  return `Budget: ${budget.spent_usd.toFixed(4)} of ${budget.ceiling_usd.toFixed(2)} USD used (estimated) · ${budget.calls_used} of ${budget.max_calls} calls`;
}

// --- static snapshot (Sprint 5) ---------------------------------------------------------

// The hosted snapshot has no server: the recorded API responses are static files next to the page.
// Only the read-only replay and evaluation endpoints exist there; anything else (live mode, reviews)
// maps to null and is never requested.
export function snapshotApiPath(path, method = "GET") {
  if (path === "/api/scenarios" && method === "GET") return "api/scenarios.json";
  if (path === "/api/evaluation" && method === "GET") return "api/evaluation.json";
  const m = /^\/api\/scenarios\/([A-Za-z0-9]{1,16})\/replay$/.exec(path);
  if (m && method === "POST") return `api/scenarios/${m[1]}/replay.json`;
  return null;
}

// --- structural probes (Sprint 5) -------------------------------------------------------

export const PROBE_LABELS = {
  gold: "Correct proposal (the case's annotated relationship and quotation)",
  paraphrased_quote: "Quotation paraphrased instead of copied",
  fabricated_target: "Invented target document",
  reversed_direction: "Direction reversed (source and target swapped)",
  quote_from_unrelated_provision: "Extra quotation from an unrelated provision",
};

export const REJECTION_REASON_LABELS = {
  quote_not_found: "quotation not found in the cited provision",
  unknown_target: "target is not among the documents given to the model",
  no_source_evidence: "no quotation from the source provision",
  quote_outside_related: "quotation lies outside the source and target provisions",
};

const EXPECTATION_ORDER = { accept: 0, reject: 1, undetectable: 2 };
export const EXPECTATION_LABELS = {
  accept: "Published",
  reject: "Rejected",
  undetectable: "Should be rejected; structural checks cannot tell",
};

function probeLabel(probe) {
  if (PROBE_LABELS[probe]) return PROBE_LABELS[probe];
  const wrong = /^wrong_type:(\w+)$/.exec(probe);
  if (wrong) return `Wrong relation type (${wrong[1]}), real quotation`;
  if (/^forbidden_\d+_with_real_quote$/.test(probe)) return "Relationship the case forbids, backed by a real quotation";
  return probe;
}

// Rows for the probe table, grouped by display label, computed only from /api/evaluation.
export function probeRows(structural) {
  const groups = new Map();
  for (const g of (structural && structural.by_probe) || []) {
    const label = probeLabel(g.probe);
    const row = groups.get(label) || { label, expectation: g.expectation, total: 0, published: 0, rejected: 0, reasons: {} };
    row.total += g.total;
    row.published += g.published;
    row.rejected += g.rejected;
    for (const [r, n] of Object.entries(g.rejection_reasons || {})) row.reasons[r] = (row.reasons[r] || 0) + n;
    groups.set(label, row);
  }
  return [...groups.values()]
    .sort((a, b) => (EXPECTATION_ORDER[a.expectation] ?? 3) - (EXPECTATION_ORDER[b.expectation] ?? 3))
    .map((row) => ({
      label: row.label,
      expected: EXPECTATION_LABELS[row.expectation] || row.expectation,
      outcome: row.expectation === "reject" ? `${row.rejected} of ${row.total} rejected`
        : row.expectation === "undetectable" ? `${row.published} of ${row.total} published as candidates`
          : `${row.published} of ${row.total} published`,
      reason: Object.keys(row.reasons).map((r) => REJECTION_REASON_LABELS[r] || r).join("; ") || (row.expectation === "undetectable" ? "passes every structural check" : "—"),
      kind: row.expectation === "undetectable" ? "limit" : row.expectation === "reject" ? "caught" : "ok",
    }));
}

// One line for the first screen: what the validators catch and what they cannot.
export function checksSummary(structural) {
  const rows = (structural && structural.by_probe) || [];
  if (!rows.length) return null;
  const sum = (exp, field) => rows.filter((g) => g.expectation === exp).reduce((n, g) => n + g[field], 0);
  return {
    caught: `${sum("reject", "rejected")} of ${sum("reject", "total")} faulty proposals rejected`,
    missed: `${sum("undetectable", "published")} of ${sum("undetectable", "total")} semantically wrong proposals still published`,
  };
}
