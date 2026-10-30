# Sprint 2: handoff for independent review (PR #4)

Branch: `feat/sprint-2-investigation`. Review target: the PR head. Do not merge before PM sign-off.

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

## Reproduce

```bash
cd regulagraph && pip install -e ".[dev]"
python scripts/fetch_corpus.py           # PDFs + SHA-256 check
pytest                                    # offline; live test skipped unless RUN_LIVE_META=1
python scripts/run_eval.py structural     # validator probes + retrieval recall (no model)
python scripts/run_eval.py rescore eval/results/live_*.json   # re-score recorded live output (no model)
```
