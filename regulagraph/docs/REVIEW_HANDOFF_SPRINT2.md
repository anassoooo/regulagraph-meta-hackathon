# Sprint 2: handoff for independent review (PR #4)

Branch: `feat/sprint-2-investigation`. Review target: the PR head. Do not merge before PM sign-off.

## Remediation after the review of `0b3f0a2`

| Finding | Fix | Verify with |
|---|---|---|
| **P1: evaluation false positives.** One correct relationship hid an incorrect one | Scorer v2 (`SCORING_VERSION = 2`, `score_case`). Every published relationship gets a verdict (`match`, `type_error`, `direction_error`, `target_error`, `citation_insufficient`, `unscored`). A case is correct only if it has ≥1 `match` and no error verdict. Several acceptable types (repeals + supersedes) may each be a `match` | `pytest tests/test_evaluation.py -k "same_endpoints or multiple_acceptable or reversed_one or confusable"` |
| **P1: insufficient citation fragments.** Any 1-character overlap passed | A cited evidence span must **contain** the annotated supporting span (`evidence_quote`) or an annotated `sufficient_quotes` alternative (same document and page). This is a location check, not semantic support | `pytest tests/test_evaluation.py -k "fragment or sufficient"` (includes `"ab"`) |
| **P1: credential redaction** | `app/core/redaction.py`. The configured key and `Bearer <token>` are redacted at the provider error boundary (`meta_client._provider_error`), in every `ModelCallRecord` and parsed item (`investigator._call_model`), in log records (`install_log_redaction`: app and scripts), and in every file the scripts write | `pytest tests/test_redaction.py`. A MockTransport provider echoes a dummy key in its error message, type and header. The tests check the exception, `to_dict`, `InvestigationOutcome` JSON, logs, `_write` output and the full `run_live` path |
| **P2: evaluation consistency** | Live files keep the run's own `score` unchanged (historical). `rescored` is replaced with the scorer v2 result (`scoring_version: 2`). `eval/results/current_scoring_summary.json` is the single current summary. `eval/README.md` separates the raw call facts (A), current v2 metrics (B) and superseded historical scores (C), and documents the E13 re-run and the E10 truncation | `python scripts/run_eval.py rescore eval/results/live_*.json`, then `git diff`: only `rescored`, `rescore_note` and the summary may change. `tests/test_evaluation.py::test_rescore_of_recorded_live_results` asserts that `score` is untouched |

### Second review of `fd9c67d`: logging redaction bypass (P1)

- **Problem:** the record factory redacted only `record.getMessage()`. Tracebacks, chained exceptions, stack info and `extra` fields are added later by the formatter, and they leaked.
- **Fix:** redaction now happens at the **final output boundary**. `install_log_redaction` wraps `logging.Handler.format` once, process-wide, so the exact string each handler emits is redacted after formatting. The early record-level layer is kept.
- **Coverage:** every handler that builds its output through `self.format(record)`: stream, file, rotating, `QueueHandler`, uvicorn's and pytest's handlers, `lastResort`, and handlers added after installation, with any formatter.
- **Verify:** `pytest tests/test_redaction.py -v`. The tests reproduce both reviewer examples plus a chained exception, a stack-info case and a custom formatter. They assert that the dummy key and the Bearer token are absent from the stream and file output, not just from `LogRecord.message`.
- **Limitations:**
  - a handler that overrides `format()` without calling `Handler.format`, or that ships raw records (`SocketHandler`/`HTTPHandler` pickle or urlencode the record), bypasses the boundary;
  - `LogRecord` objects themselves (e.g. `caplog.records`, `exc_info`) still hold the original exception object.

  No such handlers are configured by the app or the scripts.

No live calls were made for this remediation. Credentials were not changed. All relationships remain `candidate`. Arbitrary PDF upload remains unavailable: the corpus is the curated manifest only.

## Claims to check

| # | Claim | Where to check |
|---|---|---|
| 1 | Provision identity is the source span `(document_id, segments)`, and provision text equals the source spans exactly (R1) | `models/schemas.py` `Provision`; `services/provisions.py` `verify_provision*`; `tests/test_provisions.py` |
| 2 | Relationship evidence must lie inside the source or target provision, with at least one item in the source (R2) | `services/relationships.py`; `tests/test_relationships.py` |
| 3 | Nothing is ever confirmed. The model cannot set status or confidence | `RegulatoryRelationship.status`, `assert_publishable`; `ProposedRelationship` (`extra="forbid"`); `tests/test_investigator.py::test_invalid_items_are_rejected_individually` |
| 4 | Model quotes are located verbatim inside the cited provision; any miss rejects the proposal | `investigator._resolve`, `provisions.locate_in_provision` |
| 5 | Refusal, truncation, content filter, malformed output and provider errors publish nothing | `investigator._call_model`; parametrised failure tests |
| 6 | No paid call happens without `META_INFERENCE_ENABLED=true`, a key, `--confirm-paid-call` and explicit `--case` IDs. Live evaluation preflights the required provisions and does not call the API if one is missing | `scripts/run_eval.py`, `scripts/smoke_test.py`; `tests/test_evaluation.py::test_live_runner_*` |
| 7 | Recorded live results are real API responses and contain no credentials | `eval/results/smoke_*.json`, `live_*.json`; `eval/README.md` |
| 8 | Lexical validity is not presented as semantic correctness | README "Scope of verification"; `test_lexical_match_is_not_semantic_support`; structural probes |

## Known weak points (please probe)

- **Semantic errors pass validation.** A wrong relation type, or a link to a wrong but real target, is published as a `candidate`. In the recorded structural run, all 15 such probes were published (`eval/results/structural_*.json`).
- **Retrieval heuristics.** BM25, document-number bonuses, and a tiered ranking (named article → articles it cites within the same circular → BM25). The two targeted fixes (E12, E13) were tuned on this small corpus, and they may mis-rank on other layouts.
- **Segmentation fits BCT French layouts only.** Headings must start a line and carry a separator. Annexes are found only at the top of a page. Quoted text is detected only by guillemets, so 2025-01's replacement annex for 91-24 is not marked as quoted.
- **Token budget.** `max_completion_tokens` covers reasoning. E10 (two documents, 4.5k prompt tokens) spent 2,046 of 2,048 tokens reasoning and returned nothing.
- **Evaluation cases.** They were written by the implementer and have not been domain-reviewed. Scoring compares against the author's `acceptable_types`.
- **Scoring changed after a live run.** E10 was first scored "correct" because nothing was published. That is now fixed (no usable answer means not correct). Recorded files keep the original `score` and add `rescored`.
- **Still not built:**
  - **R3:** evidence is not bound to an extraction digest, and stored artifacts are not re-extracted.
  - **R5:** there is no extraction time limit and no decompression guard.
  - **No OCR.** The Arabic text is unusable for one document.
- **Licensing:** the BCT PDFs are not redistributed and their reuse terms are unclear. The project licence is still undecided.

## Live evidence summary

There were 7 requests to `muse-spark-1.3`, all HTTP 200, costing about $0.065 at published prices.

| Case | Outcome | Validated |
|---|---|---|
| Smoke test | `{"status": "ok"}` | n/a |
| E01: explicit repeal | repeals + supersedes, exact citation | yes |
| E13 pilot: qualified reference (negative) | nothing published; target not in prompt | **no** |
| E13 re-run | references only, exact citation | yes |
| E12: implements, with direction | implements 47 → 8, exact citation | yes |
| E05: "sans préjudice" | references, exact citation | yes |
| E10: overreach (negative) | truncated, no answer | **no** |

Not run live: E02, E03, E04, E06, E07, E08, E09, E11.

## Reproduce and verify (no paid calls)

```bash
cd regulagraph && pip install -e ".[dev]"
python scripts/fetch_corpus.py              # 5 PDFs + SHA-256 check (needed for real-corpus tests)
pytest                                       # expect 314 passed, 1 skipped (live test, opt-in)
pytest tests/test_redaction.py tests/test_evaluation.py -v
python scripts/run_eval.py structural        # 46 probes: 7 accept, 24 reject, 15 undetectable; recall 7/7, 5/5
python scripts/run_eval.py rescore eval/results/live_*.json
git diff --stat                              # rescoring must reproduce the committed files: no diff expected
grep -rl "dummy-secret-key" eval/ || echo "no dummy key in results"
```

To confirm no real credential is committed (the tests contain only dummy tokens), compare against the key in a local `.env` without printing it:

```bash
python -c "from dotenv import dotenv_values; import subprocess; k=dotenv_values('.env').get('MODEL_API_KEY') or ''; print('key in history:', bool(k) and k in subprocess.run(['git','log','-p','--all'],capture_output=True,text=True,errors='ignore').stdout)"
```
