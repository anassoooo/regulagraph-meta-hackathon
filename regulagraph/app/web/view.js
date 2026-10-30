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
export function buildGraph(replay, { width = 720, columnGap = 360, top = 60, rowGap = 110 } = {}) {
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
  const height = top + rowGap * Math.max(1, ...[...byColumn.values()].map((l) => l.length));
  return { nodes: [...nodes.values()], edges, width, height };
}

// Quadratic curve between two nodes; parallel edges bend progressively further.
export function edgePath(from, to, parallelIndex = 0) {
  const bend = 40 + 45 * parallelIndex;
  if (from.x === to.x) {
    const cx = from.x + 120 + bend;
    const cy = (from.y + to.y) / 2;
    return { d: `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`, labelX: cx - 50, labelY: cy };
  }
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2 - bend;
  return { d: `M ${from.x} ${from.y} Q ${mx} ${my} ${to.x} ${to.y}`, labelX: mx, labelY: (from.y + to.y) / 2 - bend / 2 - 4 };
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
