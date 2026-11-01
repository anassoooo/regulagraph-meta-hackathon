// Demo layout: with live mode on, the live form comes before the recorded scenarios. Replay-only and
// snapshot pages keep their layout. The real controller runs against the fake DOM.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FakeDocument } from "./fake_dom.mjs";

const SCENARIOS = [
  { id: "E01", title: "T1", question: "Question for E01?", primary: true, documents: [] },
  { id: "E12", title: "T2", question: "Question for E12?", primary: false, documents: [] },
];
const response = (status, body) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
};

// The picker column as in index.html: heading, scenario list, hint, then the (hidden) live panel.
function pickerMarkup(document) {
  const picker = document.createElement("aside");
  picker.className = "panel picker";
  const h2 = document.createElement("h2");
  h2.textContent = "Curated investigations";
  const list = document.createElement("ul");
  list.setAttribute("id", "scenario-list");
  const hint = document.createElement("p");
  hint.className = "hint";
  const live = document.createElement("section");
  live.setAttribute("id", "live-panel");
  live.className = "live-panel";
  live.hidden = true;
  picker.append(h2, list, hint, live);
  document.body.append(picker);
  return { picker, h2, list, hint, live };
}

let boots = 0;
async function boot({ liveEnabled, snapshot = false }) {
  const document = new FakeDocument();
  globalThis.document = document;
  if (snapshot) {
    const meta = document.createElement("meta");
    meta.setAttribute("name", "regulagraph-snapshot");
    meta.setAttribute("data-commit", "abc");
    meta.setAttribute("data-built", "2026-11-01");
    document.body.append(meta);
  }
  const parts = pickerMarkup(document);
  const requested = [];
  globalThis.fetch = (url) => {
    requested.push(url);
    if (url === "/api/scenarios" || url === "api/scenarios.json") return Promise.resolve(response(200, SCENARIOS));
    if (url === "/api/live/status") {
      return Promise.resolve(response(200, liveEnabled
        ? { enabled: true, model_id: "muse-spark-1.3", budget: { spent_usd: 0, ceiling_usd: 1, calls_used: 0, max_calls: 40 } }
        : { enabled: false, reason: "live mode is off" }));
    }
    if (url === "/api/live/documents") return Promise.resolve(response(200, [{ document_id: "doc_a", reference: "Circulaire A", title: "A" }]));
    return new Promise(() => {}); // replays and the evaluation stay pending: layout only
  };
  await import(`../../app/web/app.js?livefirst=${++boots}`);
  await flush();
  return { ...parts, requested };
}

test("live mode on: the live form is moved before the recorded scenarios, which all remain", async () => {
  const { picker, h2, list, hint, live } = await boot({ liveEnabled: true });
  assert.equal(live.hidden, false);
  assert.deepEqual(picker.children, [live, h2, list, hint], "live panel first, everything else in its original order");
  assert.ok(picker.classList.contains("live-first"));
  assert.equal(list.querySelectorAll(".scenario").length, 2, "both recorded scenarios are still listed");
  assert.equal(live.parentNode, picker);
});

test("replay-only server: layout and visibility unchanged", async () => {
  const { picker, h2, list, hint, live } = await boot({ liveEnabled: false });
  assert.equal(live.hidden, true);
  assert.deepEqual(picker.children, [h2, list, hint, live]);
  assert.ok(!picker.classList.contains("live-first"));
});

test("static snapshot: layout unchanged and live mode never requested", async () => {
  const { picker, h2, list, hint, live, requested } = await boot({ liveEnabled: true, snapshot: true });
  assert.equal(live.hidden, true);
  assert.deepEqual(picker.children, [h2, list, hint, live]);
  assert.ok(!requested.some((u) => u.includes("/api/live")));
});
