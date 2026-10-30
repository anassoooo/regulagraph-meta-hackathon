# Sprint 3: handoff for review (local replay-only demo)

Branch: `feat/sprint-3-demo`, based on `main` at `7c464ba`. Do not merge before PM sign-off.

**PM decisions applied:**

| Decision | Applied as |
|---|---|
| D1 | Replay only; no live endpoint |
| D2 | Curated scenarios only: E01 primary, E12, E05, E13 |
| D3 | Quotations, labels, pages, offsets and PDF links only; no full article text |
| D4 | Local only |
| D5 | Hash-verified pre-fetched PDFs, fail safe |
| D6 | E10 appears in the evaluation view only |

No paid Meta calls were made in Sprint 3.

## Remediation of the independent review of `8096cf1` (REQUEST CHANGES)

Closure matrix:

| Finding | Defect | Fix | Regression evidence |
|---|---|---|---|
| **P1-1** Recording integrity and evaluation consistency | Only E01 was checked against its recorded fingerprint. Changing E12's recorded output from `implements` to `repeals` returned HTTP 200 with the stored `validated=true` evaluation beside a `repeals` relationship. | `app/services/replay.py`: every curated replay (E01, E12, E05, E13) must reproduce its recording exactly (content digest, report ID, prompt provisions and prompt size, model outcome and identity, question) and publish the scenario's expected candidate relation types. The evaluation returned with a replay is **recomputed by the current scorer from that replay** and carries its content digest and recording file. The stored per-record score and the scoring-summary entry must both agree with it, or the replay fails closed (503). `ReplayOut.evaluation` is a typed model whose `meaning` states that validated is not a legal or semantic verification. | `tests/test_recording_integrity.py`: `test_e12_implements_changed_to_repeals_fails_closed` (Sol's repro); `test_e12_mutation_with_a_matching_forged_fingerprint_still_fails`; record mutations × 4 scenarios covering case ID, question, report question, model ID, run model, inference model, fingerprint, fingerprint format, report ID, generated_at, prompt size, prompt provisions (disagreeing, reordered, dropped, unknown), stored verdict, stored validated, stored scoring version; summary mutations; `test_stored_evaluation_flipped_in_both_summary_entries_fails_closed`; `test_evaluation_is_bound_to_the_verified_replay` × 4. Probe against the old and new code below. |
| **P1-2** Asynchronous scenario-selection race | A slower, superseded replay response or error could overwrite the current scenario's question, graph and evidence. | `app/web/app.js`: latest request wins. Each selection gets a monotonically increasing sequence number and aborts the previous request with `AbortController`. Superseded responses and errors are dropped. Previous evidence is cleared (and `current` reset) as soon as a new scenario is selected. A response whose `scenario.id` is not the requested one is rejected. | `tests/js/controller.test.mjs` runs the **real `app.js` controller** against a minimal fake DOM (`tests/js/fake_dom.mjs`), with responses completed in a controlled order (8 tests): E01 then E12 with E12 completing first, then E01; the reverse order; a late superseded error; an abort rejection while the current request loads; the current error not replaced by a late success; stale evidence cleared while loading; reselecting the same scenario; a mismatched response. Against the pre-fix `app.js` (`8096cf1`), 7 of 8 fail. The eighth, clearing on switch, already held. Real-browser check below. |
| **P2** Malformed and unusable recordings | Missing `model_call`, null raw output, malformed JSON or missing keys could raise an uncaught `KeyError` (HTTP 500) or be accepted. | `load_recording` validates the presence and type of every field the replay uses before use. A missing or non-object `model_call`, missing, null or empty `raw_text`, `raw_text` that is not JSON, a non-`ok` outcome, malformed `usage`, wrong types, a missing `report` or `rescored`, a malformed provisions list, an unreadable file and a summary file name outside `live_*.json` all fail as `DemoUnavailable` (503), with the failed check and "restore eval/results/ from git". Usable-but-wrong output (for example an empty answer) fails the reproduction checks. `/api/evaluation` turns malformed stored results into a 503, never a 500. `scripts/demo.py --check` validates all four recordings, then requires all four replays to reproduce exactly and re-score consistently. | `test_record_mutation_fails_closed` (P2 cases × 4 scenarios; `TestClient` re-raises any uncaught exception, so a 500 would fail the test); `test_unreadable_recording_files_fail_closed`; `test_malformed_summary_makes_the_evaluation_view_503_not_500`; `test_preflight_rejects_the_e12_mutation`; `test_preflight_reports_malformed_recordings_without_crashing`; `test_full_preflight_passes_on_the_real_corpus` (all four scenarios). |

**Probe: Sol's E12 mutation on a temporary copy of `eval/results/`** (committed files hashed before and after: unchanged; 0 network calls):

| Code | Result |
|---|---|
| `8096cf1` (before) | HTTP 200, relationships `['repeals']`, evaluation `validated: true, verdicts: ['match']` |
| this commit | HTTP 503 `demo_unavailable`, check `replay does not reproduce the recording: report fingerprint` |

**Real-browser check of the race (headless Chrome 156, CDP).** E01 was clicked with a real mouse and its replay response was held by `Fetch` interception. E12 was then clicked and allowed to complete, and then E01 was released.

- The client had already aborted E01, so Chrome reported the request as no longer deliverable.
- Before and after, the page showed only E12: the selected button, the question, the recording `live_20261030T153322Z.json`, the "implements" edge and the Article 47 → Article 8 evidence.
- 0 console errors and 0 exceptions.

The full browser-QA pass (below) was re-run on this commit with identical results.

**Not changed:** UI design, scenarios, credentials and recordings. No paid Meta calls were made.

## Claims to check

| # | Claim | Where to check |
|---|---|---|
| 1 | Replay runs the recorded Muse output through the real `investigate` → `finalize_report` pipeline, re-verifying evidence against the hash-checked PDFs | `app/services/replay.py`; `tests/test_api_demo.py::test_e01_replay_is_deterministic_and_reproduces_the_recorded_run` (equals the recorded `content_digest`) |
| 2 | Replay makes zero network calls; there is no live route | `test_api_demo.py` patches `httpx.HTTPTransport.handle_request` and `MetaModelAdapter.complete` to fail; `test_no_live_inference_route_exists` |
| 3 | Every relationship is an AI-generated, unconfirmed candidate; verified source location is reported separately from the unverified interpretation | `app/api/investigations.py` (`RelationshipOut`, `EvidenceOut.source_text_verified`, `InterpretationOut.verified=False`); `tests/js/view.test.mjs` |
| 4 | No full provision text, no credentials, and no hardcoded evidence in the client | `test_replay_never_returns_full_provision_text`, `test_replay_response_contains_no_configured_credential`, `test_frontend_has_no_inline_code_external_origins_or_hardcoded_evidence`, `test_frontend_contains_no_credentials` |
| 5 | CSP with separate JS/CSS files; no inline code; untrusted text via `textContent` | `app/main.py`; `test_security_headers_everywhere`; browser QA (no CSP violations) |
| 6 | Fails safe on a missing or corrupted corpus, or on a missing, malformed, inconsistent or tampered recording, for every scenario | `test_missing_or_corrupt_corpus_fails_safely`, `test_missing_recording_fails_safely`, `tests/test_recording_integrity.py`, `tests/test_demo_preflight.py` |
| 8 | The evaluation shown with a replay is computed from that verified replay, not from stored labels | `app/services/replay.py::_bound_evaluation`; `test_evaluation_is_bound_to_the_verified_replay` |
| 9 | Scenario selection is latest-request-wins; stale responses and errors cannot overwrite the UI | `app/web/app.js::runReplay`; `tests/js/controller.test.mjs` |
| 7 | Evaluation view shows scorer v2, with E10 truncated and cases not run live marked as such | `test_evaluation_reports_scorer_v2_and_e10_truncated` |

## Evidence: automated tests

Results at the remediation commit:

| Run | Result |
|---|---|
| pytest, Python 3.13.12, corpus present | **495 passed, 1 skipped** (the opt-in live Meta test) |
| pytest, Python 3.11.15, corpus present | **495 passed, 1 skipped** |
| pytest, Python 3.13.12, corpus PDFs removed | **402 passed, 94 skipped** (corpus-dependent tests skip) |
| `node --test tests/js/view.test.mjs tests/js/controller.test.mjs` (Node 24) | **17 / 17** (9 view, 8 controller) |
| `python scripts/demo.py --check` | passes; all four scenarios reproduce exactly and re-score as validated |
| Credential scan of the diff and new files against `.env` values | none found |

- **pytest:** all Python tests, including API, recording integrity, preflight, the E01 journey (API → client view logic under Node) and both JS test files.
- **JavaScript tests:**
  - view logic: graph layout (parallel edges distinct, arrows end at box borders, labels inside the canvas), the inspector model, state messages, safe PDF links and the evaluation rows;
  - controller: latest-request-wins scenario selection, with controlled response ordering.

## Evidence: real-browser QA

**How it was run.** Headless Google Chrome 156 (the locally installed browser) was driven over the DevTools protocol by a Node 24 script, kept outside the repo, against `uvicorn` on 127.0.0.1. Real mouse and keyboard events were used, and screenshots and console were captured. This is separate from the automated suite and is not part of CI.

| Check | Result (final pass) |
|---|---|
| Replay banner visible on both views | yes |
| E01 graph | 2 edges, distinct paths, labels not overlapping, arrowheads outside node boxes |
| Mouse selection of each edge updates the evidence panel | yes (repeals, supersedes) |
| Keyboard: Tab reaches the edges, Enter selects | yes |
| Quotation readability | 15.2 px serif, original line breaks kept, verified badge, page and offsets, official PDF link |
| Evaluation view | 13 rows; E10 "Truncated: no answer" with the token detail; structural probe explanation |
| Phone width (390 px) | no page-level horizontal scroll; graph keeps a 12 px label size and scrolls inside its box |
| E12 single-column layout | arrow visible, ends outside the boxes |
| Console errors, exceptions, CSP violations, failed requests | none |

**Issues found and fixed during QA:**

1. **Arrowheads hidden under the target box.** Edges now end at box borders.
2. **The E01 "repeals" label sat on the "supersedes" curve.** The canvas and column gap are now wider, and parallel bend spacing is 60 px.
3. **Phone width: graph labels rendered at 5.4 px.** The graph now keeps its natural size and scrolls inside its box, with a narrow-screen hint.
4. **Grid min-width let the graph widen the phone viewport.** Fixed with `min-width: 0` on the panels.
5. **Favicon 404.** A local SVG favicon was added.

The browser QA was automated rather than done by hand on a physical device. Firefox and Safari were not tested.

## Known limitations

- Replay covers only the four recorded investigations, so the demo cannot answer new questions. There is no deployment.
- Graph labels on desktop render at about 9.5 px when the graph is scaled into its panel. They are legible but small.
- Model quality is still measured only on 5 live cases (4 validated, E10 truncated). Semantic errors cannot be detected structurally (15 of 15 probes published).
- The evaluation annotations have not been domain-reviewed.
- Recording integrity proves that a replay matches what was recorded and scored. It cannot detect a recording that was forged consistently in all places: model output, fingerprint, report ID, stored score and summary. The recordings are protected by git history, not by signatures.
- The `expected_relations` per scenario in `app/services/replay.py` are curated backend expectations (sorted relation types). They are not shown to the user, and the client still hardcodes no evidence.
- The JS controller tests use a minimal fake DOM, not a real browser. The real-browser race check was a single automated run, not part of CI.

## Reproduce

```bash
cd regulagraph && pip install -e ".[dev]"
python scripts/fetch_corpus.py
pytest                                  # Python + JS (node required for the JS/journey tests)
node --test tests/js/view.test.mjs tests/js/controller.test.mjs
python scripts/demo.py --check          # preflight only
python scripts/demo.py                  # http://127.0.0.1:8000, then follow the README demo script
```
