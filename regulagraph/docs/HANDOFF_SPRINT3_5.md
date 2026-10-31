# Sprint 3.5 handoff: local live investigation with review

**Scope (PM conditional GO):**

- local-only live mode, off by default;
- a free-text question over any subset of the three French BCT circulars;
- server-side enforcement of the approved documents;
- a real Muse call;
- the existing evidence verification and candidate rendering;
- append-only review records;
- spending controls, plus security and regression tests;
- one live smoke test.

**Not in scope:** UI polish, Neo4j, and full-corpus ingestion. No new dependencies were added.

**Commits on `feat/sprint-3.5-live-hitl`, based on `main` at `5beecb4`:**

| Commit | Content |
|---|---|
| `771f5c0` (P0) | Live mode, document allowlist, prompt and report checks, spending ledger, request protections, live panel, tests, golden replay hashes |
| P1 (the commit adding this file) | Review records (service, API, inspector controls), tests, docs |

P1 was started only after the full P0 gate passed: 764 passed, 1 skipped, on Python 3.11 and 3.13.

## Design

| Concern | Where | Mechanism |
|---|---|---|
| Live off by default | `config.py`, `scripts/demo.py`, `live.require_enabled` | Needs `LIVE_MODE_ENABLED`, `META_INFERENCE_ENABLED` and `MODEL_API_KEY`. `demo.py` forces the first two to false unless run with `--live --confirm-paid-calls`, and `--live` refuses a non-loopback `--host`. |
| Document allowlist | `live.resolve_allowlist`, `live.build_scope` | 1 to 3 distinct IDs, each one of the manifest's French `investigation` documents (Arabic and unknown IDs give 422). `investigate()` receives only those documents and their provisions, so retrieval, prompt, targets and finalization cannot see others. |
| Question vs source context | `live.check_prompt` | The prompt must start with exactly `Question: <question>` followed by the documents block. Only the text after the question is checked. Every `<document>` and `<provision>` there must be approved, and no excluded document or provision ID may appear. The question may name any circular. A failed check means nothing is sent and no budget is reserved. |
| Report check | `live.check_outcome` | Every document, provision, evidence span and target in the finalized report must be approved, and every relationship must be `candidate`. |
| Evidence | unchanged `investigate()`, `finalize_report()` | Same verification as replay. Truncated, refused or malformed answers publish nothing. |
| Spending | `live.Ledger`, `live.GuardedAdapter` | One call at a time per process (409 otherwise). The worst case (prompt UTF-8 bytes + 64 tokens of input, the completion cap as output, at published prices) is reserved in `var/live/ledger.jsonl` before the call. The call is refused (429) if it could pass the ceiling (default $4.50, settings max $5.00) or the call cap (40). The reservation is then settled from the reported usage, or kept if no usage was reported or the call failed. An unsettled reservation counts as spent. The ledger is hash-chained, and any edit blocks all calls (503). |
| Browser protections | `app/api/live.py` | Loopback client only. Host header must be `127.0.0.1`, `localhost` or `[::1]` (DNS rebinding). POSTs must be `application/json` (415 for form or text types), carry this server's exact `Origin`, not have `Sec-Fetch-Site` other than `same-origin`, and include `"confirm_paid_call": true`. Live checks run before the body is read. |
| Immutable runs | `live.save_run`, `live.load_run` | Written once with exclusive create, redacted. Loading re-validates the `FinalizedReport` (digest, canonical form, no confirmed relationships). |
| Reviews | `live.add_review`, `/api/live/investigations/{report_id}/reviews` | Appended to `var/live/reviews.jsonl` (hash-chained), bound to `report_id`, `content_digest` and a relationship of that run. Decisions are `accepted`, `rejected` or `needs_more_evidence`, plus a note of at most 1,000 characters. Each record carries `relationship_status: "candidate"` and `legal_confirmation: false`, and the UI says "not a legal confirmation". Reviews never modify the run. |
| Replay unchanged | `investigations.report_parts` | The report-to-response mapping was moved into a shared helper. `tests/test_replay_golden.py` pins the SHA-256 and length of the four replay responses, `/api/scenarios` and `/api/evaluation`. They were captured on `main` before any change and are identical on Python 3.11 and 3.13. |

## Security evidence

**Automated tests** (`tests/test_live.py`, `tests/js/live.test.mjs`). The real HTTP transport and `MetaModelAdapter.complete` are patched to fail; a fake model records every prompt.

| Threat | Tests |
|---|---|
| Live call when disabled | `test_live_mode_is_off_by_default_and_nothing_runs` (no adapter built, no ledger created); `test_each_switch_is_required` (each of the 3 switches off → 403) |
| Excluded document in the source context | `test_retrieval_context_holds_only_approved_documents` covers all 7 non-empty subsets. Its question names all three circulars; each excluded document ID and every excluded provision ID is absent from the context; prompt and report hold only approved documents. |
| Question vs context | `test_excluded_document_text_is_not_sent_even_when_the_question_quotes_it` (a sentence from 2018-16 is in the question; no 2018-16 provision in the context) |
| Model targets an excluded document | `test_answer_targeting_an_excluded_document_is_rejected` (the recorded E01 answer with 2018-16 excluded gives `unknown_target` and no relationship) |
| Defence in depth | `test_prompt_check_refuses_excluded_context_before_reserving_or_calling` (4 tampered prompts; no call, no reservation); `test_outcome_check_refuses_a_report_outside_the_scope` |
| Invalid approval | Arabic, unknown, duplicate, empty, non-list or non-string IDs → 422 with zero calls |
| Unconfirmed paid call | missing, `false`, `"true"` or `1` → 422 with zero calls; the UI clears the confirmation box after each call |
| CSRF and DNS rebinding | missing, foreign or other-port `Origin`; `Sec-Fetch-Site` of `cross-site` or `same-site`; `text/plain`, form or multipart bodies; non-loopback client; foreign Host header → 403/415 with zero calls |
| Overspending | reserve/settle accounting; refusal when the worst case exceeds the remaining budget; call cap; cumulative ceiling over 12 calls with no reported usage (spent ≤ ceiling); a failed call keeps its worst case; an unsettled reservation counts as spent; a ceiling above $5 is rejected by settings; `demo.py --live` refuses a public host and a budget above 5 |
| Concurrency | `test_concurrent_requests_make_one_call` (the second request gets `LiveBusy`; one reservation) |
| Ledger or review tampering | an edited, deleted or garbage ledger line blocks calls (status shows the reason); an edited review log gives 503 |
| Credentials | a key echoed in model output is absent from the response and from every stored file |
| Review integrity | separate record; run file bytes unchanged; status stays `candidate`; `legal_confirmation` false; decisions `confirmed` and `legally_confirmed` → 422; wrong digest, foreign relationship, other run's digest → 422; unknown or malformed run → 404; tampered run → 503; review POSTs need the same protections; reviewing makes no model call |
| Frontend | live controls only when the server reports live mode; nothing sent without a question, a document and confirmation; exact document IDs sent; a superseded live result is dropped; a response for another question is refused; review controls only on live results; a refused review is shown as not recorded |

**Mutation check of the allowlist** (run during development, not committed):

| Mutation | Result |
|---|---|
| `investigate()` given every document | 8 of 10 allowlist tests fail; the 2 that pass have nothing excluded |
| The same, with the prompt and report checks also removed | 9 of 10 fail |

**Real server probes** (`demo.py --live` on 127.0.0.1:8012, before the paid call): foreign `Origin` 403, no `Origin` 403, `text/plain` 415, rebinding Host 403, `Sec-Fetch-Site: cross-site` 403, no confirmation 422, unknown document 422, replay 200. The ledger was still empty afterwards: no spend, and the file was not created.

## Replay integrity evidence

| Check | Result |
|---|---|
| Four replay responses vs `main` | **Byte-identical** (SHA-256 and length) after P0 and after P1, in replay-only and live mode (`tests/test_replay_golden.py`) |
| `/api/scenarios`, `/api/evaluation` | Byte-identical |
| `eval/results/` | Unchanged: no diff against `main`; hashed before and after every live test (autouse fixture) |
| Existing suites | `test_recording_integrity.py`, `test_api_demo.py`, `test_demo_preflight.py`, `test_journey.py`, `controller.test.mjs` unchanged and passing |
| `python scripts/demo.py --check` | Passes; all four scenarios reproduce exactly |

**Intentional change to an existing test.** `test_api_demo.py::test_no_live_inference_route_exists` asserted that no live route exists (Sprint 3 decision D1). It is now `test_live_inference_exists_only_under_api_live_and_is_off_by_default`:

- live routes exist only under `/api/live/`;
- the replay route has no live variant;
- `POST /api/live/investigations` returns 403 `live_mode_disabled` by default.

## Live smoke test (authorised, run once)

```
POST /api/live/investigations
question: "Quelle circulaire la circulaire n°2026-10 abroge-t-elle ou remplace-t-elle ?"
document_ids: [2026-10, 2018-16]   (2025-01 excluded)
```

| Field | Value |
|---|---|
| HTTP | 200 in 10.6 s |
| `model_id` | `muse-spark-1.3` |
| `finish_reason` | `length` |
| Outcome | **truncated** |
| Tokens | 3,260 prompt; 2,048 completion (1,637 reasoning) |
| Context | 8 provisions, 11,696 prompt characters, all from the two approved documents |
| Relationships published | 0, the intended fail-closed outcome for a truncated answer |
| Ledger | reserved $0.02387, settled at **$0.01278** (estimated) |
| Key | absent from the response and from `var/live/` |

The call exercised:

- the real provider path;
- the allowlist checks;
- reservation and settlement;
- immutable storage;
- the truncation fail-closed rule.

It did **not** produce relationships, because Muse spent its whole completion cap. The recorded E01 run used 1,922 of 2,048 tokens, so this question sits at the edge of the cap.

**Total additional Meta spend: $0.01278 (estimated), 1 call.**

## Remaining limitations

1. **Truncation at the default cap.** At `META_MAX_COMPLETION_TOKENS=2048` and `reasoning_effort=low`, live answers can be truncated, as the smoke test was. Raising the cap for live mode to 4096 would increase the worst case per call by about $0.009. Not changed without PM approval.
2. **Live relationships and reviews on a real answer are not yet demonstrated.** They are covered by tests using the recorded E01 answer through the full pipeline, but not by a successful live call. A second smoke call needs PM approval.
3. **Costs are estimates.** They are computed from reported usage at published prices (`PRICE_PER_M_*` in `evaluation.py`, checked 2026-10-30), not from invoices.
4. **The ledger is local.** It guards this checkout's `var/live/`. Deleting the directory resets the budget, and so does a second checkout or the provider's console. It is a guard against accidental overspend, not an authority.
5. **Single process.** The one-call-at-a-time lock and ledger serialisation are per process; `demo.py` runs one uvicorn process.
6. **Single local reviewer.** Reviews are not authenticated: whoever can reach 127.0.0.1 can review. There is no reviewer identity.
7. **The model may still name an excluded circular from its own knowledge** in its interpretation text. No excluded provision or document is in the prompt, and none can become evidence, a provision or a target.
8. **Aborted requests still cost.** If a newer selection supersedes a live run in the UI, the in-flight call still completes, is charged and is stored; only its display is dropped.
9. **The Sprint 3 static banner says REPLAY MODE.** In live mode a second, red LIVE MODE banner is added above the panel. UI polish is deferred.

## Local demo commands

```bash
cd regulagraph
python scripts/fetch_corpus.py                       # once: corpus PDFs, SHA-256 verified
python scripts/demo.py                               # replay only (unchanged), http://127.0.0.1:8000
python scripts/demo.py --check                       # preflight only
python scripts/demo.py --live --confirm-paid-calls   # live mode, budget 4.50 USD (cumulative, var/live/ledger.jsonl)
python scripts/demo.py --live --confirm-paid-calls --budget-usd 1 --port 8001
```

**Live walkthrough:**

1. Note the red LIVE MODE banner.
2. In "Live investigation", tick the source documents.
3. Type a question.
4. Tick "I confirm one paid Meta call".
5. Click Run.
6. Inspect the candidates, then record a review in the evidence panel.

The budget line shows the estimated spend.

## Test results (P1 head)

| Run | Result |
|---|---|
| pytest, Python 3.13.12 | **780 passed, 1 skipped** (opt-in live Meta test) |
| pytest, Python 3.11.15 | **780 passed, 1 skipped** |
| pytest, Python 3.13.12, corpus PDFs removed | **615 passed, 166 skipped** (corpus-dependent tests skip; no failures) |
| `node --test tests/js/view.test.mjs tests/js/controller.test.mjs tests/js/live.test.mjs` | **32 / 32** |
| `ruff check app tests scripts` | clean |
| Golden replay capture vs `main` | byte-identical; `eval/results/` unchanged |
