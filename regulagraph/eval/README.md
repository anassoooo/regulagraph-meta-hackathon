# Evaluation

`cases.json` holds 13 manually written relationship cases over the French corpus in `data/manifest.json`: 7 positive and 6 negative. E13 was added for the live pilot: it is the negative counterpart of E04, a qualified reference.

- **Phenomena covered:**
  - explicit repeal;
  - direction and type;
  - negation and qualified references;
  - repeated labels;
  - an extraction artefact;
  - historical references and targets outside the corpus;
  - provision-level overreach;
  - an unrelated pair.
- **Gold answers:** each positive case names the gold source provision, the target, the acceptable relation types and a gold evidence quote, copied verbatim from the extracted text and verified by `tests/test_evaluation.py`.
- **Negative cases** list the relationships that must not be published.

**Review status:** the cases were written during implementation by reading the extracted text. They have **not** been reviewed by a domain expert (`review.reviewed_by: null`). The expected relations are a reading of the text, not a legal opinion.

## Runs

### Structural probes and retrieval recall: no model

Command: `python scripts/run_eval.py structural`. Recorded run: `results/structural_20261030T152059Z.json`, made after the retrieval fixes described below. It used corpus PDFs fetched on 2026-10-30, pypdf 6.21.2, and the default `META_MAX_PROMPT_CHARS=24000`.

The probes are hand-made proposals replayed through the real pipeline (`investigate` → `finalize_report`). They measure **what validation does**, not what Muse does.

| Probe | Expected | Observed |
|---|---|---|
| Gold answer (7 positive cases) | published | 7 / 7 published |
| Paraphrased quote | rejected | 7 / 7 rejected (`quote_not_found`) |
| Fabricated target document ID | rejected | 7 / 7 rejected (`unknown_target`) |
| Reversed direction (provision targets) | rejected | 5 / 5 rejected (`no_source_evidence`) |
| Extra quote from an unrelated provision | rejected | 5 / 5 rejected (`quote_outside_related`) |
| Wrong relation type with the gold quote | *cannot be detected* | 7 / 7 **published as candidates** |
| Forbidden link backed by a real quote (negative cases) | *cannot be detected* | 8 / 8 **published as candidates** |

The last two rows are the measured limit of structural validation. When a semantically wrong relationship cites a real passage from its own source provision, it passes every check and is published as a `candidate`. Only the model's judgement, or a human reviewer, stands between that proposal and the report.

Retrieval (deterministic BM25 plus bonuses, case question only, documents scoped per case):

| Case | Gold source retrieved | Gold target retrieved |
|---|---|---|
| E01, E03 | yes | (whole document) |
| E04, E05, E06, E07, E12 | yes | yes |

Gold source: 7 of 7. Gold provision target: 5 of 5. The named source and target provisions of the negative cases are also retrieved (`tests/test_evaluation.py`).

Two targeted retrieval fixes, both covered by offline regression tests:

1. **E12 (target 4/5 → 5/5).** The question names Article 47, and Article 47 cites "l’article 8 de la présente circulaire". Article 8 shares no words with the question, so it was never retrieved. Fix: body articles cited within the same circular by an article the question names get the named-article bonus. This is one hop, and annex articles with the same number are excluded.
2. **E13, a regression introduced by fix 1 and found while analysing the live pilot.** Article 35 cites Articles 23, 29 and 17. The cited articles 23 and 29 then outranked Article 17, which the question names, and pushed it out of the top 8. Fix: ranking is tiered, with named articles first, then the articles they cite, then BM25 order.

### Live Muse results: real API calls, not replays

All live calls went to `https://api.meta.ai/v1/chat/completions` with the locally configured key, after the PM confirmed in the Meta dashboard that the hackathon credits were active. The files hold the request bounds, the provider response ID, the raw model output and the finalized report. They never hold the key or headers. Costs are estimated from the published prices ($1.25 / $4.25 per million input / output tokens; reasoning tokens are counted as output).

| File | Call | HTTP | finish | Input tok | Output tok (reasoning) | Latency | Approx. cost |
|---|---|---|---|---|---|---|---|
| `results/smoke_20261030T145559Z.json` | smoke test, 64-token cap, `reasoning_effort=minimal` | 200 | stop | 69 | 58 (43) | 5.6 s | $0.0003 |
| `results/live_20261030T151756Z.json` | **E01** pilot, 2,048 cap, `low` | 200 | stop | 3,260 | 1,922 (1,374) | 8.9 s | $0.012 |
| `results/live_20261030T151822Z.json` | **E13** pilot, 2,048 cap, `low` | 200 | stop | 3,307 | 1,467 (1,453) | 7.9 s | $0.010 |

**E01 (explicit repeal, positive): correct.**

- **Retrieval:** Article 52 was in the 8 retrieved provisions; the target is a whole document.
- **Proposals:** Muse returned 2: `repeals` and `supersedes`, from Article 52 to circular 2018-16. Both are acceptable types.
- **Validation:** both were accepted as `candidate`, and 0 were rejected.
- **Evidence:** both quote the full Article 52 sentence verbatim, line breaks included. It was located at page 33, characters 1158-1428, and overlaps the gold quote, so `citation_ok` is true. A `lexical_match` was recorded.

**E13 (negation / qualified reference, negative): passed, with a caveat.**

- **Answer:** Muse returned `{"relationships": []}`, so no forbidden relationship (amends, repeals, supersedes or conflicts_with from Article 35 to Article 17) was published.
- **Caveat:** in this call **Article 17 was not in the prompt**. That was the retrieval regression described above, fixed after the call. Only the source, Article 35, which contains the qualifying sentence, was shown.
- **Conclusion:** the pass shows that Muse did not invent a relationship from the qualified sentence. It does **not** show how Muse classifies the relationship when the target is visible. A re-run of E13 with the fixed retrieval would answer that, and has not been made.

**Observed contract and behaviour:**

- the strict `json_schema` response format was honoured;
- no refusals, no truncation and no API errors occurred;
- reasoning tokens are 70-99 % of completion tokens at `low`. At the old 1,024 default cap, E01 (1,922 completion tokens) would have been truncated and published nothing. **Use a cap of at least 2,048.**

**Not run:** the remaining 11 cases (E02-E12). Two pilot cases are not a benchmark, and no accuracy figure is claimed.
