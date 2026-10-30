// Unit tests for the pure view logic: `node --test tests/js/view.test.mjs`
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NODE_HALF_WIDTH,
  buildGraph,
  edgePath,
  errorMessage,
  evaluationRows,
  inspectorModel,
  safePdfUrl,
  stateMessage,
} from "../../app/web/view.js";

// Synthetic fixture shaped like POST /api/scenarios/{id}/replay (not real BCT text).
const replay = {
  mode: "replay",
  verified_meaning: "Verified source text: location only.",
  state: "ok",
  documents: [
    { document_id: "doc_a", reference: "Circulaire n°X-1", source_url: "https://www.bct.gov.tn/a.pdf" },
    { document_id: "doc_b", reference: "Circulaire n°X-2", source_url: "https://www.bct.gov.tn/b.pdf" },
  ],
  provisions: [{ provision_id: "prov_1", document_id: "doc_a", kind: "article", label: "Article 9", page_start: 4, page_end: 4 }],
  relationships: [
    {
      relationship_id: "rel_1", relation_type: "repeals", source_provision_id: "prov_1", target_kind: "document",
      target_id: "doc_b", status: "candidate", confirmed: false, ai_generated: true, notice: "AI candidate notice",
      target_mentioned_in_quote: true,
      evidence: [{ evidence_id: "ev_1", document_id: "doc_a", page_number: 4, char_start: 10, char_end: 30,
                   quote: "<img src=x onerror=alert(1)> quoted", source_text_verified: true,
                   pdf_url: "https://www.bct.gov.tn/a.pdf#page=4" }],
      interpretation: { text: "model says", model_id: "muse-spark-1.3", verified: false },
    },
    {
      relationship_id: "rel_2", relation_type: "supersedes", source_provision_id: "prov_1", target_kind: "document",
      target_id: "doc_b", status: "candidate", confirmed: false, ai_generated: true, notice: "AI candidate notice",
      target_mentioned_in_quote: false,
      evidence: [{ evidence_id: "ev_1", document_id: "doc_a", page_number: 4, char_start: 10, char_end: 30,
                   quote: "q", source_text_verified: false, pdf_url: "javascript:alert(1)" }],
      interpretation: null,
    },
  ],
};

test("graph has one provision node, one whole-document node and two parallel edges", () => {
  const g = buildGraph(replay);
  assert.deepEqual(g.nodes.map((n) => [n.id, n.kind]), [["prov_1", "provision"], ["doc_b", "document"]]);
  assert.deepEqual(g.edges.map((e) => [e.from, e.to, e.label, e.parallelIndex]),
    [["prov_1", "doc_b", "repeals", 0], ["prov_1", "doc_b", "supersedes", 1]]);
  const [prov, doc] = g.nodes;
  assert.ok(prov.x < doc.x, "source document is laid out left of the target");
  const a = edgePath(prov, doc, 0);
  const b = edgePath(prov, doc, 1);
  assert.notEqual(a.d, b.d, "parallel edges must not overlap");
  // Edges run between box borders, so the arrowhead is not hidden under the target box.
  const [, x0] = a.d.split(" ");
  const parts = a.d.split(" ");
  assert.equal(Number(x0), prov.x + NODE_HALF_WIDTH);
  assert.ok(Number(parts[parts.length - 2]) <= doc.x - NODE_HALF_WIDTH, "edge ends before the target box");
  assert.ok(a.labelY > 0 && b.labelY > 0, "labels stay inside the canvas");
});

test("graph is deterministic", () => {
  assert.deepEqual(buildGraph(replay), buildGraph(structuredClone(replay)));
});

test("single-document internal reference lays out in one column", () => {
  const internal = {
    ...replay,
    documents: [replay.documents[0]],
    provisions: [replay.provisions[0], { provision_id: "prov_2", document_id: "doc_a", kind: "article", label: "Article 3", page_start: 2, page_end: 2 }],
    relationships: [{ ...replay.relationships[0], target_kind: "provision", target_id: "prov_2" }],
  };
  const g = buildGraph(internal);
  assert.equal(new Set(g.nodes.map((n) => n.x)).size, 1);
  const byY = [...g.nodes].sort((a, b) => a.y - b.y).map((n) => n.label);
  assert.deepEqual(byY, ["Article 3", "Article 9"]);  // rows follow page order
});

test("inspector shows candidate badges, verified location and an unverified interpretation", () => {
  const m = inspectorModel(replay, "rel_1");
  assert.deepEqual(m.badges, ["AI-generated", "Candidate", "Unconfirmed"]);
  assert.equal(m.heading, "Circulaire n°X-1, Article 9 repeals Circulaire n°X-2 (whole circular)");
  assert.equal(m.evidence[0].location, "Circulaire n°X-1, page 4, characters 10–30");
  assert.equal(m.evidence[0].verifiedLabel, "Source text verified (exact location)");
  assert.equal(m.evidence[0].quote, "<img src=x onerror=alert(1)> quoted", "quote is passed through as text, never parsed");
  assert.match(m.interpretation.label, /not verified/);
  assert.match(m.lexicalNote, /not proof/);
});

test("inspector never hides unverified evidence and drops unsafe links", () => {
  const m = inspectorModel(replay, "rel_2");
  assert.equal(m.evidence[0].verified, false);
  assert.equal(m.evidence[0].verifiedLabel, "Source text NOT verified");
  assert.equal(m.evidence[0].pdfUrl, null);
  assert.equal(m.interpretation, null);
  assert.equal(inspectorModel(replay, "rel_missing"), null);
});

test("only official BCT links are allowed", () => {
  assert.equal(safePdfUrl("https://www.bct.gov.tn/x.pdf#page=2"), "https://www.bct.gov.tn/x.pdf#page=2");
  for (const bad of ["javascript:alert(1)", "http://www.bct.gov.tn/x.pdf", "https://evil.example/www.bct.gov.tn/", null, 3]) {
    assert.equal(safePdfUrl(bad), null);
  }
});

test("result states: ok shows nothing, others explain that nothing was published", () => {
  assert.equal(stateMessage("ok"), null);
  for (const s of ["empty", "truncated", "refused", "content_filter", "malformed", "provider_error"]) {
    const m = stateMessage(s);
    assert.ok(m && m.title && /nothing was published/i.test(m.body), s);
  }
  assert.equal(stateMessage("truncated").title, "Truncated: no answer");
  assert.equal(stateMessage("bogus").kind, "error");
});

test("error responses map to safe messages", () => {
  assert.equal(errorMessage(503, { error: { message: "corpus unavailable" } }).title, "Demo data unavailable");
  assert.equal(errorMessage(404, null).title, "Scenario not found");
  assert.equal(errorMessage(500, null).body, "HTTP 500");
});

test("evaluation rows show truncation honestly", () => {
  const rows = evaluationRows({ cases: [
    { id: "E10", phenomenon: "provision_level_overreach", polarity: "negative", status: "truncated", finish_reason: "length", completion_tokens: 2048, reasoning_tokens: 2046, errors: ["model_outcome:truncated"] },
    { id: "E01", phenomenon: "explicit_repeal", polarity: "positive", status: "validated", errors: [] },
    { id: "E02", phenomenon: "direction", polarity: "negative", status: "not_run", errors: [] },
  ] });
  assert.deepEqual(rows.map((r) => r.statusLabel), ["Truncated: no answer", "Validated (annotation matched)", "Not run live"]);
  assert.equal(rows[0].detail, "finish_reason=length; 2046 of 2048 completion tokens were reasoning");
  assert.equal(rows[0].phenomenon, "provision level overreach");
});
