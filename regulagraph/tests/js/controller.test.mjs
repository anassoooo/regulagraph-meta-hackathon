// Controller-level regression tests for app/web/app.js: scenario selection is latest-request-wins.
// The real controller runs against a minimal fake DOM, with fetch replaced by a stub whose
// responses the test completes in a chosen order.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FakeDocument } from "./fake_dom.mjs";

const SCENARIOS = [
  { id: "E01", title: "Explicit repeal", question: "Question for E01?", primary: true, documents: [] },
  { id: "E12", title: "Implementation", question: "Question for E12?", primary: false, documents: [] },
];

// Synthetic replay payloads (test fixtures, not demo data): every visible value is scenario-specific.
function replayFor(id, relationType) {
  const doc = `doc_${id}`;
  return {
    mode: "replay",
    verified_meaning: "location only",
    scenario: { id, question: `Question for ${id}?` },
    recording: { file: `live_${id}.json`, recorded_at: "2026-10-30T00:00:00Z", model_id: "model", prompt_tokens: 1,
      completion_tokens: 1, reasoning_tokens: 0 },
    state: "ok",
    documents: [{ document_id: doc, reference: `Circular ${id}`, source_url: null }],
    provisions: [
      { provision_id: `src_${id}`, document_id: doc, kind: "article", label: `Article S${id}`, page_start: 1, page_end: 1 },
      { provision_id: `tgt_${id}`, document_id: doc, kind: "article", label: `Article T${id}`, page_start: 2, page_end: 2 },
    ],
    relationships: [{
      relationship_id: `rel_${id}`, relation_type: relationType, source_provision_id: `src_${id}`,
      target_kind: "provision", target_id: `tgt_${id}`, status: "candidate", notice: "candidate",
      target_mentioned_in_quote: true, interpretation: { text: `Interpretation ${id}`, model_id: "model" },
      evidence: [{ evidence_id: `ev_${id}`, document_id: doc, page_number: 3, char_start: 10, char_end: 20,
        quote: `Quote only in ${id}`, source_text_verified: true, pdf_url: null }],
    }],
    rejected: [],
    content_digest: `digest_${id}`,
  };
}

const response = (status, body) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
};

let boots = 0;
async function boot() {
  const document = new FakeDocument();
  globalThis.document = document;
  const pending = new Map();
  globalThis.fetch = (url, options = {}) => {
    if (url === "/api/scenarios") return Promise.resolve(response(200, SCENARIOS));
    const id = /\/api\/scenarios\/(\w+)\/replay/.exec(url)[1];
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    pending.set(id, { resolve, reject, signal: options.signal });
    return promise;
  };
  await import(`../../app/web/app.js?boot=${++boots}`); // a fresh controller per test
  await flush();
  const $ = (id) => document.getElementById(id);
  return {
    pending,
    select: async (id) => {
      document.querySelector(`[data-scenario="${id}"]`).click();
      await flush();
    },
    succeed: async (id, relationType) => {
      pending.get(id).resolve(response(200, replayFor(id, relationType)));
      await flush();
    },
    fail: async (id, status = 503) => {
      pending.get(id).resolve(response(status, { error: { code: "demo_unavailable", message: `Failure of ${id}` } }));
      await flush();
    },
    ui: () => ({
      selected: document.querySelectorAll(".scenario.selected").map((b) => b.getAttribute("data-scenario")),
      question: $("question").textContent,
      recording: $("recording").textContent,
      edges: document.querySelectorAll("#graph .edge").map((e) => e.getAttribute("data-relationship")),
      list: document.querySelectorAll("#relationship-list .rel").map((e) => e.getAttribute("data-relationship")),
      inspector: $("inspector").textContent,
      state: $("state").hidden ? null : $("state").textContent,
    }),
  };
}

function assertShowsOnly(ui, id, other) {
  assert.deepEqual(ui.selected, [id]);
  assert.match(ui.question, new RegExp(`Question for ${id}\\?`));
  assert.match(ui.recording, new RegExp(`live_${id}\\.json`));
  assert.deepEqual(ui.edges, [`rel_${id}`]);
  assert.deepEqual(ui.list, [`rel_${id}`]);
  assert.match(ui.inspector, new RegExp(`Quote only in ${id}`));
  assert.equal(ui.state, null);
  const all = JSON.stringify(ui);
  for (const marker of [`Question for ${other}`, `live_${other}`, `rel_${other}`, `Quote only in ${other}`]) {
    assert.ok(!all.includes(marker), `stale ${other} content shown: ${marker}`);
  }
}

test("E01 then E12, E12 completes first, then E01: only E12 is shown", async () => {
  const app = await boot();
  await app.select("E01");
  await app.select("E12");
  assert.equal(app.pending.get("E01").signal.aborted, true, "the superseded request is aborted");
  await app.succeed("E12", "implements");
  assertShowsOnly(app.ui(), "E12", "E01");
  await app.succeed("E01", "repeals"); // late, superseded response
  assertShowsOnly(app.ui(), "E12", "E01");
});

test("E01 then E12, E01 completes first, then E12: E01 never appears, then E12 is shown", async () => {
  const app = await boot();
  await app.select("E01");
  await app.select("E12");
  await app.succeed("E01", "repeals");
  const loading = app.ui();
  assert.deepEqual(loading.selected, ["E12"]);
  assert.match(loading.question, /Question for E12\?/);
  assert.match(loading.state, /Replaying/);
  assert.deepEqual(loading.edges, []);
  assert.ok(!JSON.stringify(loading).includes("E01"), "stale E01 content shown while E12 loads");
  await app.succeed("E12", "implements");
  assertShowsOnly(app.ui(), "E12", "E01");
});

test("a late error from a superseded request does not overwrite the current result", async () => {
  const app = await boot();
  await app.select("E01");
  await app.select("E12");
  await app.succeed("E12", "implements");
  await app.fail("E01");
  assertShowsOnly(app.ui(), "E12", "E01");
});

test("a superseded error that arrives while the current request loads is ignored", async () => {
  const app = await boot();
  await app.select("E01");
  await app.select("E12");
  app.pending.get("E01").reject(Object.assign(new Error("aborted"), { name: "AbortError" })); // what fetch does on abort
  await flush();
  assert.match(app.ui().state, /Replaying/);
  await app.succeed("E12", "implements");
  assertShowsOnly(app.ui(), "E12", "E01");
});

test("the current request's error is shown and a late superseded success cannot replace it", async () => {
  const app = await boot();
  await app.select("E01");
  await app.select("E12");
  await app.fail("E12");
  await app.succeed("E01", "repeals");
  const ui = app.ui();
  assert.deepEqual(ui.selected, ["E12"]);
  assert.match(ui.state, /Demo data unavailable.*Failure of E12/);
  assert.deepEqual(ui.edges, []);
  assert.ok(!JSON.stringify(ui).includes("E01"));
});

test("switching scenarios clears the previous evidence while the new replay loads", async () => {
  const app = await boot();
  await app.select("E01");
  await app.succeed("E01", "repeals");
  assertShowsOnly(app.ui(), "E01", "E12");
  await app.select("E12");
  const ui = app.ui();
  assert.deepEqual(ui.edges, []);
  assert.deepEqual(ui.list, []);
  assert.match(ui.inspector, /Select a relationship/);
  assert.ok(!JSON.stringify(ui).includes("E01"), "stale E01 evidence shown while E12 loads");
});

test("reselecting the same scenario: only the latest response is rendered", async () => {
  const app = await boot();
  await app.select("E12");
  const first = app.pending.get("E12");
  await app.select("E12");
  first.resolve(response(200, replayFor("E01", "repeals"))); // a stale payload on the superseded request
  await flush();
  assert.match(app.ui().state, /Replaying/);
  await app.succeed("E12", "implements");
  assertShowsOnly(app.ui(), "E12", "E01");
});

test("a response for a different scenario than the one requested is rejected", async () => {
  const app = await boot();
  await app.select("E12");
  app.pending.get("E12").resolve(response(200, replayFor("E01", "repeals")));
  await flush();
  const ui = app.ui();
  assert.match(ui.state, /does not belong to the selected scenario/);
  assert.deepEqual(ui.edges, []);
});
