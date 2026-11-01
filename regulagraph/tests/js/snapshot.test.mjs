// Sprint 5: static snapshot mode and the structural-probe presentation.
// The controller runs against the fake DOM; fetch is a stub that records every URL and method.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FakeDocument } from "./fake_dom.mjs";
import { checksSummary, modeBanner, probeRows, snapshotApiPath } from "../../app/web/view.js";

// Synthetic structural block in the shape of /api/evaluation (test fixture, not demo data).
const STRUCTURAL = {
  probes: 9, accept_total: 2, reject_total: 4, undetectable_total: 3, undetectable_published: 3,
  probe_origin: "Hand-authored test proposals, replayed through the real validation pipeline with no model call. They are not Muse output.",
  source_file: "structural_fixture.json",
  undetectable_meaning: "m",
  by_probe: [
    { probe: "gold", expectation: "accept", total: 2, published: 2, rejected: 0, as_expected: 2, rejection_reasons: {} },
    { probe: "wrong_type:amends", expectation: "undetectable", total: 1, published: 1, rejected: 0, as_expected: 1, rejection_reasons: {} },
    { probe: "paraphrased_quote", expectation: "reject", total: 3, published: 0, rejected: 3, as_expected: 3, rejection_reasons: { quote_not_found: 3 } },
    { probe: "forbidden_0_with_real_quote", expectation: "undetectable", total: 1, published: 1, rejected: 0, as_expected: 1, rejection_reasons: {} },
    { probe: "forbidden_1_with_real_quote", expectation: "undetectable", total: 1, published: 1, rejected: 0, as_expected: 1, rejection_reasons: {} },
    { probe: "new_kind", expectation: "reject", total: 1, published: 0, rejected: 1, as_expected: 1, rejection_reasons: { odd_reason: 1 } },
  ],
};

test("snapshot paths: only the read-only replay and evaluation responses exist", () => {
  assert.equal(snapshotApiPath("/api/scenarios"), "api/scenarios.json");
  assert.equal(snapshotApiPath("/api/evaluation"), "api/evaluation.json");
  assert.equal(snapshotApiPath("/api/scenarios/E01/replay", "POST"), "api/scenarios/E01/replay.json");
  for (const [path, method] of [["/api/live/status", "GET"], ["/api/live/investigations", "POST"], ["/api/scenarios/E01/replay", "GET"],
    ["/api/scenarios/../x/replay", "POST"], ["/api/scenarios/E01/replay?x", "POST"], ["/api/live/investigations/r/reviews", "POST"]]) {
    assert.equal(snapshotApiPath(path, method), null, `${method} ${path}`);
  }
});

test("snapshot banner: states the build commit and date, and that nothing is re-verified in the browser", () => {
  const b = modeBanner({ snapshot: { commit: "abc1234", builtAt: "2026-11-01" }, liveEnabled: true, view: "live" });
  assert.equal(b.kind, "snapshot");
  assert.match(b.text, /^STATIC SNAPSHOT · /);
  assert.match(b.text, /commit abc1234 on 2026-11-01/);
  assert.match(b.text, /verified .* when this snapshot was built, not re-verified in your browser/);
  assert.match(b.text, /No model and no server are called/);
  assert.doesNotMatch(b.text, /LIVE/);
});

test("probe rows: grouped and ordered, wrong-but-quoted proposals shown as the limitation, unknown kinds kept raw", () => {
  const rows = probeRows(STRUCTURAL);
  assert.deepEqual(rows.map((r) => [r.label, r.outcome, r.kind]), [
    ["Correct proposal (the case's annotated relationship and quotation)", "2 of 2 published", "ok"],
    ["Quotation paraphrased instead of copied", "3 of 3 rejected", "caught"],
    ["new_kind", "1 of 1 rejected", "caught"],
    ["Wrong relation type (amends), real quotation", "1 of 1 published as candidates", "limit"],
    ["Relationship the case forbids, backed by a real quotation", "2 of 2 published as candidates", "limit"],
  ]);
  assert.equal(rows[1].reason, "quotation not found in the cited provision");
  assert.equal(rows[2].reason, "odd_reason");
  assert.equal(rows[3].reason, "passes every structural check");
  assert.deepEqual(probeRows(null), []);
  assert.deepEqual(probeRows({ probes: 1 }), [], "an older response without by_probe shows no table");
});

test("first-screen line: counts come only from the response", () => {
  assert.deepEqual(checksSummary(STRUCTURAL), {
    caught: "4 of 4 faulty proposals rejected",
    missed: "3 of 3 semantically wrong proposals still published",
  });
  assert.equal(checksSummary(null), null);
});

// --- controller in snapshot mode ---------------------------------------------------------

const SCENARIOS = [{ id: "E01", title: "T", question: "Question for E01?", primary: true, documents: [] }];
function replayE01() {
  return {
    mode: "replay", verified_meaning: "location only", scenario: { id: "E01", question: "Question for E01?" },
    recording: { file: "live_E01.json", recorded_at: "2026-10-30T00:00:00Z", model_id: "model", prompt_tokens: 1, completion_tokens: 1, reasoning_tokens: 0 },
    state: "ok",
    documents: [{ document_id: "d", reference: "Circular X", source_url: null }],
    provisions: [
      { provision_id: "s", document_id: "d", kind: "article", label: "Article S", page_start: 1, page_end: 1 },
      { provision_id: "t", document_id: "d", kind: "article", label: "Article T", page_start: 2, page_end: 2 },
    ],
    relationships: [{
      relationship_id: "rel", relation_type: "repeals", source_provision_id: "s", target_kind: "provision", target_id: "t",
      status: "candidate", notice: "candidate", target_mentioned_in_quote: true, interpretation: null,
      evidence: [{ evidence_id: "e", document_id: "d", page_number: 3, char_start: 1, char_end: 2, quote: "Snapshot quote", source_text_verified: true, pdf_url: null }],
    }],
    rejected: [], content_digest: "digest",
  };
}
const response = (status, body) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
};

let boots = 0;
async function bootSnapshot() {
  const document = new FakeDocument();
  globalThis.document = document;
  const meta = document.createElement("meta");
  meta.setAttribute("name", "regulagraph-snapshot");
  meta.setAttribute("data-commit", "abc1234");
  meta.setAttribute("data-built", "2026-11-01");
  document.body.append(meta);
  document.getElementById("eval-table").append(document.createElement("tbody")); // static markup in index.html
  const calls = [];
  const files = {
    "api/scenarios.json": SCENARIOS,
    "api/scenarios/E01/replay.json": replayE01(),
    "api/evaluation.json": { scoring_version: 2, meaning: "m", cases: [], structural: STRUCTURAL },
  };
  globalThis.fetch = (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    return Promise.resolve(url in files ? response(200, files[url]) : response(404, {}));
  };
  await import(`../../app/web/app.js?snapshot=${++boots}`);
  await flush();
  return { document, calls, $: (id) => document.getElementById(id) };
}

test("snapshot controller: reads static files with GET only, never contacts live endpoints, labels the page", async () => {
  const { calls, $ } = await bootSnapshot();
  assert.deepEqual(calls.map((c) => `${c.method} ${c.url}`).sort(),
    ["GET api/evaluation.json", "GET api/scenarios.json", "GET api/scenarios/E01/replay.json"]);
  assert.ok(calls.every((c) => c.body === undefined));
  assert.ok(!calls.some((c) => c.url.includes("/api/")), "no server API path is requested");
  assert.match($("mode-banner").textContent, /^STATIC SNAPSHOT · .*commit abc1234 on 2026-11-01/);
  assert.match($("inspector").textContent, /Snapshot quote/);
  assert.match($("checks-summary").textContent, /hand-authored proposals, not Muse output/);
  assert.match($("checks-summary").textContent, /4 of 4 faulty proposals rejected · 3 of 3 semantically wrong proposals still published/);
});

test("snapshot controller: evaluation view renders the hand-authored probe table", async () => {
  const { $ } = await bootSnapshot();
  $("checks-summary").querySelector("button").click();
  await flush();
  const text = $("eval-structural").textContent;
  assert.match(text, /^Hand-authored test proposals.*not Muse output\. Recorded run: structural_fixture\.json\./);
  assert.match(text, /Quotation paraphrased instead of copied.*3 of 3 rejected.*quotation not found in the cited provision/);
  assert.match(text, /Relationship the case forbids, backed by a real quotation.*2 of 2 published as candidates.*passes every structural check/);
  assert.match($("mode-banner").textContent, /^STATIC SNAPSHOT/, "the banner stays on the snapshot notice in the evaluation view");
});

test("every quotation carries the extraction note; the quotation text itself is unchanged", async () => {
  const { $ } = await bootSnapshot();
  const notes = $("inspector").querySelectorAll(".extraction-note");
  assert.equal(notes.length, 1, "one note per quotation");
  assert.match(notes[0].textContent, /exactly as extracted from the PDF text layer.*can differ from the printed page/);
  assert.equal($("inspector").querySelector("blockquote").textContent, "Snapshot quote");
});
