// Controller tests for the Sprint 3.5 live panel in app/web/app.js, against the fake DOM.
// fetch is a stub; live responses are synthetic fixtures, completed in a chosen order.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FakeDocument } from "./fake_dom.mjs";

const DOCS = [
  { document_id: "d-one", reference: "Circular one", title: "First", publication_date: "2026-01-01" },
  { document_id: "d-two", reference: "Circular two", title: "Second", publication_date: "2026-01-02" },
  { document_id: "d-three", reference: "Circular three", title: "Third", publication_date: "2026-01-03" },
];
const SCENARIOS = [{ id: "E01", title: "Explicit repeal", question: "Question for E01?", primary: true, documents: [] }];
const BUDGET = { ceiling_usd: 4.5, spent_usd: 0.0123, remaining_usd: 4.4877, calls_used: 1, max_calls: 40 };

function liveFor(question, tag) {
  return {
    mode: "live", notice: "live", verified_meaning: "location only", question,
    approved_document_ids: ["d-one"], created_at: "2026-10-31T00:00:00Z",
    call: { model_id: "muse-spark-1.3", outcome: "ok", prompt_tokens: 100, completion_tokens: 50, reasoning_tokens: 10,
      estimated_cost_usd: 0.0003 },
    state: "ok",
    documents: [{ document_id: "d-one", reference: "Circular one", source_url: null }],
    provisions: [
      { provision_id: `s-${tag}`, document_id: "d-one", kind: "article", label: `Article S${tag}`, page_start: 1, page_end: 1 },
      { provision_id: `t-${tag}`, document_id: "d-one", kind: "article", label: `Article T${tag}`, page_start: 2, page_end: 2 },
    ],
    relationships: [{
      relationship_id: `rel-${tag}`, relation_type: "amends", source_provision_id: `s-${tag}`, target_kind: "provision",
      target_id: `t-${tag}`, status: "candidate", notice: "candidate", target_mentioned_in_quote: false,
      interpretation: null,
      evidence: [{ evidence_id: `ev-${tag}`, document_id: "d-one", page_number: 1, char_start: 1, char_end: 5,
        quote: `Live quote ${tag}`, source_text_verified: true, pdf_url: null }],
    }],
    rejected: [], report_id: `rep-${tag}`, content_digest: `digest-${tag}`, budget: BUDGET,
    review_meaning: "A review is not a legal confirmation.",
  };
}

const response = (status, body) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
};

let boots = 0;
async function boot({ enabled = true, storedReviews = [], deferReviewGets = false } = {}) {
  const document = new FakeDocument();
  globalThis.document = document;
  const posts = [];
  const reviewPosts = [];
  const reviewGets = [];
  const pendingReplay = new Map();
  globalThis.fetch = (url, options = {}) => {
    if (url === "/api/scenarios") return Promise.resolve(response(200, SCENARIOS));
    if (url === "/api/live/status") {
      return Promise.resolve(response(200, { enabled, reason: enabled ? null : "live mode is off", model_id: "muse-spark-1.3",
        max_question_chars: 500, budget: enabled ? { ...BUDGET, spent_usd: 0, calls_used: 0 } : null }));
    }
    if (url === "/api/live/documents") return Promise.resolve(response(enabled ? 200 : 403, enabled ? DOCS : {}));
    if (url === "/api/live/investigations") {
      let resolve;
      const promise = new Promise((res) => {
        resolve = res;
      });
      posts.push({ options, body: JSON.parse(options.body), resolve, signal: options.signal });
      return promise;
    }
    const review = /\/api\/live\/investigations\/([\w-]+)\/reviews$/.exec(url);
    if (review && options.method === "POST") {
      let resolve;
      const promise = new Promise((res) => {
        resolve = res;
      });
      reviewPosts.push({ url, body: JSON.parse(options.body), headers: options.headers, resolve });
      return promise;
    }
    if (review) {
      const digest = review[1].replace(/^rep-/, "digest-");
      const listing = (reviews) => response(200, { report_id: review[1], content_digest: digest, meaning: "m", reviews });
      if (deferReviewGets) {
        return new Promise((res) => reviewGets.push({ url, resolve: (reviews) => res(listing(reviews)) }));
      }
      return Promise.resolve(listing(storedReviews));
    }
    const m = /\/api\/scenarios\/(\w+)\/replay/.exec(url);
    if (m) {
      return new Promise((res) => pendingReplay.set(m[1], res));
    }
    return Promise.resolve(response(404, {}));
  };
  await import(`../../app/web/app.js?live=${++boots}`);
  await flush();
  const $ = (id) => document.getElementById(id);
  const docBoxes = () => document.querySelectorAll("[data-live-doc]");
  return {
    $, posts, reviewPosts, reviewGets, pendingReplay, docBoxes,
    fill: ({ question = "Which circular amends which?", docs = ["d-one"], confirm = true } = {}) => {
      $("live-question").value = question;
      for (const box of docBoxes()) box.checked = docs.includes(box.getAttribute("data-live-doc"));
      $("live-confirm").checked = confirm;
    },
    run: async () => {
      $("live-run").click();
      await flush();
    },
    ui: () => ({
      recording: $("recording").textContent,
      edges: document.querySelectorAll("#graph .edge").map((e) => e.getAttribute("data-relationship")),
      inspector: $("inspector").textContent,
      state: $("state").hidden ? null : $("state").textContent,
      question: $("question").textContent,
    }),
  };
}

test("replay-only server: no live controls are rendered", async () => {
  const app = await boot({ enabled: false });
  assert.equal(app.docBoxes().length, 0);
  assert.equal(app.$("live-banner").textContent, "");
  assert.equal(app.posts.length, 0);
});

test("live server: banner, three unselected documents, budget", async () => {
  const app = await boot();
  assert.match(app.$("live-banner").textContent, /LIVE MODE ENABLED.*paid Meta Muse call/);
  assert.equal(app.$("live-banner").hidden, false);
  assert.deepEqual(app.docBoxes().map((b) => b.getAttribute("data-live-doc")), ["d-one", "d-two", "d-three"]);
  assert.ok(app.docBoxes().every((b) => b.checked !== true), "no document is approved by default");
  assert.match(app.$("live-budget").textContent, /0\.0000 of 4\.50 USD/);
  assert.equal(app.$("live-panel").hidden, false);
});

for (const [name, fill, message] of [
  ["no question", { question: "  " }, /Enter a question/],
  ["no document", { docs: [] }, /Select at least one source document/],
  ["not confirmed", { confirm: false }, /Confirm the paid Meta call/],
]) {
  test(`nothing is sent without ${name}`, async () => {
    const app = await boot();
    app.fill(fill);
    await app.run();
    assert.equal(app.posts.length, 0);
    assert.match(app.ui().state, message);
  });
}

test("sends exactly the selected documents as JSON with confirmation, then needs a fresh confirmation", async () => {
  const app = await boot();
  app.fill({ docs: ["d-one", "d-three"] });
  await app.run();
  assert.equal(app.posts.length, 1);
  const post = app.posts[0];
  assert.equal(post.options.method, "POST");
  assert.equal(post.options.headers["Content-Type"], "application/json");
  assert.deepEqual(post.body, { question: "Which circular amends which?", document_ids: ["d-one", "d-three"],
    confirm_paid_call: true });
  assert.equal(app.$("live-confirm").checked, false);
  assert.match(app.ui().state, /Calling Meta Muse \(paid\)/);
  await app.run(); // clicking again without re-confirming sends nothing
  assert.equal(app.posts.length, 1);
});

test("a live result renders as AI candidates with the live badge, cost and budget", async () => {
  const app = await boot();
  app.fill();
  await app.run();
  app.posts[0].resolve(response(200, liveFor("Which circular amends which?", "A")));
  await flush();
  const ui = app.ui();
  assert.match(ui.recording, /^Live .*muse-spark-1\.3 .*~0\.0003 USD \(estimated\) · 1 approved source document/);
  assert.deepEqual(ui.edges, ["rel-A"]);
  assert.match(ui.inspector, /Live quote A/);
  assert.match(ui.inspector, /AI-generatedCandidateUnconfirmed/);
  assert.match(app.$("live-budget").textContent, /0\.0123 of 4\.50 USD .* 1 of 40 calls/);
});

test("a live result arriving after a newer scenario selection is dropped", async () => {
  const app = await boot();
  app.fill();
  await app.run();
  app.$("scenario-list").querySelector('[data-scenario="E01"]').click();
  await flush();
  assert.equal(app.posts[0].signal.aborted, true);
  app.posts[0].resolve(response(200, liveFor("Which circular amends which?", "LATE")));
  await flush();
  const ui = app.ui();
  assert.ok(!JSON.stringify(ui).includes("LATE"), "superseded live result shown");
  assert.match(ui.question, /Question for E01/);
});

test("a response for a different question is refused", async () => {
  const app = await boot();
  app.fill();
  await app.run();
  app.posts[0].resolve(response(200, liveFor("Some other question?", "X")));
  await flush();
  assert.match(app.ui().state, /does not match the request/);
  assert.deepEqual(app.ui().edges, []);
});

test("server refusals are shown, not swallowed", async () => {
  const app = await boot();
  app.fill();
  await app.run();
  app.posts[0].resolve(response(429, { error: { code: "live_budget_exhausted", message: "The live budget could be exceeded" } }));
  await flush();
  assert.match(app.ui().state, /The live budget could be exceeded/);
});

// --- P1: review controls --------------------------------------------------------------------

async function liveShown(options) {
  const app = await boot(options);
  app.fill();
  await app.run();
  app.posts[0].resolve(response(200, liveFor("Which circular amends which?", "A")));
  await flush();
  return app;
}

const reviewRecord = (decision, at = "2026-10-31T10:00:00Z", sequence = 1, tag = "A") => ({
  review_id: `r${sequence}`, report_id: `rep-${tag}`, content_digest: `digest-${tag}`, relationship_id: `rel-${tag}`,
  decision, note: "", reviewed_at: at, relationship_status: "candidate", legal_confirmation: false, sequence,
});

test("live results show review controls with the not-a-legal-confirmation notice", async () => {
  const app = await liveShown();
  const buttons = app.$("inspector").querySelectorAll("[data-review]").map((b) => b.getAttribute("data-review"));
  assert.deepEqual(buttons, ["accepted", "rejected", "needs_more_evidence"]);
  assert.match(app.ui().inspector, /A review is not a legal confirmation/);
  assert.match(app.ui().inspector, /No review recorded/);
});

test("existing reviews are loaded and the latest one is shown", async () => {
  const app = await liveShown({ storedReviews: [reviewRecord("rejected", "T1", 1), reviewRecord("accepted", "T2", 2)] });
  assert.match(app.ui().inspector, /Reviewer accepted · T2\. Still an AI-generated candidate: not a legal confirmation/);
});

test("accepting sends the run's digest and relationship, then shows the recorded decision", async () => {
  const app = await liveShown();
  app.$("review-note").value = "Checked p.3";
  app.$("inspector").querySelector('[data-review="accepted"]').click();
  await flush();
  assert.equal(app.reviewPosts.length, 1);
  const post = app.reviewPosts[0];
  assert.equal(post.url, "/api/live/investigations/rep-A/reviews");
  assert.equal(post.headers["Content-Type"], "application/json");
  assert.deepEqual(post.body, { relationship_id: "rel-A", content_digest: "digest-A", decision: "accepted",
    note: "Checked p.3" });
  post.resolve(response(200, reviewRecord("accepted")));
  await flush();
  assert.match(app.ui().inspector, /Reviewer accepted · 2026-10-31T10:00:00Z/);
  assert.match(app.ui().inspector, /AI-generatedCandidateUnconfirmed/, "badges unchanged by a review");
  assert.equal(app.posts.length, 1, "reviewing never starts a live call");
});

test("a refused review is reported and nothing is shown as recorded", async () => {
  const app = await liveShown();
  app.$("inspector").querySelector('[data-review="rejected"]').click();
  await flush();
  app.reviewPosts[0].resolve(response(422, { error: { message: "The review does not match this run's content digest." } }));
  await flush();
  assert.match(app.$("review-error").textContent, /Review not recorded.*content digest/);
  assert.match(app.ui().inspector, /No review recorded/);
});

test("replay results never show review controls", async () => {
  const app = await boot();
  app.$("scenario-list").querySelector('[data-scenario="E01"]').click();
  await flush();
  app.pendingReplay.get("E01")(response(200, {
    ...liveFor("Question for E01?", "R"), mode: "replay", scenario: { id: "E01" },
    recording: { file: "live_E01.json", recorded_at: "t", model_id: "m" },
  }));
  await flush();
  assert.match(app.ui().inspector, /Live quote R/);
  assert.equal(app.$("inspector").querySelectorAll("[data-review]").length, 0);
});

// --- remediation: review-response ordering (Sol finding 2) ------------------------------------

const clickReview = (app, decision) => app.$("inspector").querySelector(`[data-review="${decision}"]`).click();

test("race 1: a delayed initial reviews GET cannot overwrite a newer acceptance", async () => {
  const app = await liveShown({ deferReviewGets: true });
  assert.equal(app.reviewGets.length, 1, "the initial GET is still in flight");
  clickReview(app, "accepted");
  await flush();
  app.reviewPosts[0].resolve(response(200, reviewRecord("accepted", "T1", 1)));
  await flush();
  assert.match(app.ui().inspector, /Reviewer accepted · T1/);
  assert.equal(app.reviewGets.length, 2, "a reconciling GET follows the write");
  app.reviewGets[0].resolve([]); // the stale initial GET arrives last, from before the write
  await flush();
  assert.match(app.ui().inspector, /Reviewer accepted · T1/);
  assert.doesNotMatch(app.ui().inspector, /No review recorded/);
  app.reviewGets[1].resolve([reviewRecord("accepted", "T1", 1)]);
  await flush();
  assert.match(app.ui().inspector, /Reviewer accepted · T1/);
});

test("race 2: two quick decisions are appended in click order and the later one stays shown", async () => {
  const app = await liveShown({ deferReviewGets: true });
  clickReview(app, "accepted");
  clickReview(app, "rejected");
  await flush();
  assert.equal(app.reviewPosts.length, 1, "the second decision waits for the first to be recorded");
  assert.equal(app.reviewPosts[0].body.decision, "accepted");
  app.reviewPosts[0].resolve(response(200, reviewRecord("accepted", "T1", 1)));
  await flush();
  assert.equal(app.reviewPosts.length, 2);
  assert.equal(app.reviewPosts[1].body.decision, "rejected");
  app.reviewPosts[1].resolve(response(200, reviewRecord("rejected", "T2", 2)));
  await flush();
  assert.match(app.ui().inspector, /Reviewer rejected · T2/);
  // Reads delivered newest first, then older ones: the display never moves back to the acceptance.
  const [initial, afterFirst, afterSecond] = app.reviewGets;
  afterSecond.resolve([reviewRecord("accepted", "T1", 1), reviewRecord("rejected", "T2", 2)]);
  await flush();
  afterFirst.resolve([reviewRecord("accepted", "T1", 1)]);
  await flush();
  initial.resolve([]);
  await flush();
  assert.match(app.ui().inspector, /Reviewer rejected · T2/);
});

test("reversed delivery of write responses keeps the higher server sequence", async () => {
  const app = await liveShown({ deferReviewGets: true });
  clickReview(app, "accepted");
  await flush();
  // e.g. another tab recorded a later rejection; its listing arrives before this write's response
  app.reviewGets[0].resolve([reviewRecord("rejected", "T2", 2)]);
  await flush();
  app.reviewPosts[0].resolve(response(200, reviewRecord("accepted", "T1", 1)));
  await flush();
  assert.match(app.ui().inspector, /Reviewer rejected · T2/);
});

test("a review response for a superseded live run is never applied to the new one", async () => {
  const app = await liveShown();
  clickReview(app, "accepted");
  clickReview(app, "rejected"); // queued behind the first
  await flush();
  app.fill();
  await app.run();
  app.posts[1].resolve(response(200, liveFor("Which circular amends which?", "B")));
  await flush();
  assert.match(app.ui().inspector, /Live quote B/);
  app.reviewPosts[0].resolve(response(200, reviewRecord("accepted", "T1", 1)));
  await flush();
  assert.match(app.ui().inspector, /Live quote B/);
  assert.match(app.ui().inspector, /No review recorded/);
  assert.equal(app.reviewPosts.length, 1, "the queued decision for the old run is not sent");
  clickReview(app, "accepted");
  await flush();
  assert.equal(app.reviewPosts[1].url, "/api/live/investigations/rep-B/reviews");
  assert.equal(app.reviewPosts[1].body.content_digest, "digest-B");
  app.reviewPosts[1].resolve(response(200, reviewRecord("accepted", "T3", 2, "A"))); // wrong run in the body
  await flush();
  assert.match(app.ui().inspector, /No review recorded/);
});

test("a review response arriving after switching to a scenario replay is dropped", async () => {
  const app = await liveShown();
  clickReview(app, "accepted");
  await flush();
  app.$("scenario-list").querySelector('[data-scenario="E01"]').click();
  await flush();
  app.reviewPosts[0].resolve(response(200, reviewRecord("accepted", "T1", 1)));
  await flush();
  assert.doesNotMatch(app.ui().inspector, /Reviewer accepted/);
  assert.equal(app.$("inspector").querySelectorAll("[data-review]").length, 0);
});

test("mergeReviews keeps the highest sequence per relationship and ignores other runs", async () => {
  const { mergeReviews } = await import("../../app/web/view.js");
  const a1 = reviewRecord("accepted", "T1", 1);
  const r2 = reviewRecord("rejected", "T2", 2);
  assert.equal(mergeReviews(new Map(), [r2, a1], "rep-A", "digest-A").get("rel-A"), r2);
  assert.equal(mergeReviews(mergeReviews(new Map(), [r2], "rep-A", "digest-A"), [a1], "rep-A", "digest-A").get("rel-A"), r2);
  assert.equal(mergeReviews(new Map(), [reviewRecord("accepted", "T", 1, "B")], "rep-A", "digest-A").size, 0);
  assert.equal(mergeReviews(new Map(), [{ ...a1, content_digest: "other" }], "rep-A", "digest-A").size, 0);
  for (const sequence of [undefined, 0, -1, 1.5, "3"]) {
    assert.equal(mergeReviews(new Map(), [{ ...a1, sequence }], "rep-A", "digest-A").size, 0);
  }
});
