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

## Claims to check

| # | Claim | Where to check |
|---|---|---|
| 1 | Replay runs the recorded Muse output through the real `investigate` → `finalize_report` pipeline, re-verifying evidence against the hash-checked PDFs | `app/services/replay.py`; `tests/test_api_demo.py::test_e01_replay_is_deterministic_and_reproduces_the_recorded_run` (equals the recorded `content_digest`) |
| 2 | Replay makes zero network calls; there is no live route | `test_api_demo.py` patches `httpx.HTTPTransport.handle_request` and `MetaModelAdapter.complete` to fail; `test_no_live_inference_route_exists` |
| 3 | Every relationship is an AI-generated, unconfirmed candidate; verified source location is reported separately from the unverified interpretation | `app/api/investigations.py` (`RelationshipOut`, `EvidenceOut.source_text_verified`, `InterpretationOut.verified=False`); `tests/js/view.test.mjs` |
| 4 | No full provision text, no credentials, and no hardcoded evidence in the client | `test_replay_never_returns_full_provision_text`, `test_replay_response_contains_no_configured_credential`, `test_frontend_has_no_inline_code_external_origins_or_hardcoded_evidence`, `test_frontend_contains_no_credentials` |
| 5 | CSP with separate JS/CSS files; no inline code; untrusted text via `textContent` | `app/main.py`; `test_security_headers_everywhere`; browser QA (no CSP violations) |
| 6 | Fails safe on a missing or corrupted corpus or recording | `test_missing_or_corrupt_corpus_fails_safely`, `test_missing_recording_fails_safely`, `tests/test_demo_preflight.py` |
| 7 | Evaluation view shows scorer v2, with E10 truncated and cases not run live marked as such | `test_evaluation_reports_scorer_v2_and_e10_truncated` |

## Evidence: automated tests

- **pytest:** all Python tests, including API, preflight, the E01 journey (API → client view logic under Node) and `node --test tests/js/view.test.mjs`. Exact counts are in the PR.
- **JavaScript unit tests:** cover graph layout (parallel edges distinct, arrows end at box borders, labels inside the canvas), the inspector model, state messages, safe PDF links and the evaluation rows.

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

## Reproduce

```bash
cd regulagraph && pip install -e ".[dev]"
python scripts/fetch_corpus.py
pytest                                  # Python + JS (node required for the JS/journey tests)
node --test tests/js/view.test.mjs
python scripts/demo.py --check          # preflight only
python scripts/demo.py                  # http://127.0.0.1:8000, then follow the README demo script
```
