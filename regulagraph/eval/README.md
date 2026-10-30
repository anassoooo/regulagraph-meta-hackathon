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

All live calls went to `https://api.meta.ai/v1/chat/completions` with the locally configured key, after the PM confirmed in the Meta dashboard that the hackathon credits were active. Each case was one call with no retry. Every file and the summary pass through credential redaction before they are written. They hold the request bounds, the provider response ID, the raw model output and the finalized report, never the key or headers.

#### A. Raw call record (facts, independent of any scorer)

Costs are estimated from the published prices ($1.25 / $4.25 per million input / output tokens; reasoning tokens are counted as output).

| File | Call | HTTP | finish | Input tok | Output tok (reasoning) | Latency | ≈ cost |
|---|---|---|---|---|---|---|---|
| `smoke_20261030T145559Z.json` | smoke test, 64-token cap, `minimal` | 200 | stop | 69 | 58 (43) | 5.6 s | $0.0003 |
| `live_20261030T151756Z.json` | E01 | 200 | stop | 3,260 | 1,922 (1,374) | 8.9 s | $0.012 |
| `live_20261030T151822Z.json` | E13, first pilot | 200 | stop | 3,307 | 1,467 (1,453) | 7.9 s | $0.010 |
| `live_20261030T153246Z.json` | E13 re-run | 200 | stop | 3,298 | 1,359 (1,129) | 6.3 s | $0.010 |
| `live_20261030T153322Z.json` | E12 | 200 | stop | 3,261 | 1,170 (983) | 6.9 s | $0.009 |
| `live_20261030T153355Z.json` | E05 | 200 | stop | 3,834 | 936 (762) | 8.0 s | $0.009 |
| `live_20261030T153423Z.json` | E10 | 200 | **length** | 4,511 | 2,048 (2,046) | 9.6 s | $0.014 |

7 requests, about **$0.065** in total. Calls after the smoke test used `reasoning_effort=low` and a 2,048-token cap.

#### B. Current scorer: **version 2** (authoritative)

Source: [`results/current_scoring_summary.json`](results/current_scoring_summary.json), regenerated by `python scripts/run_eval.py rescore results/live_*.json`. The rules are in `SCORING_VERSION` / `score_case` in `app/services/evaluation.py`:

- every published relationship gets a verdict;
- one wrong relationship makes the case incorrect, even if another relationship is right;
- a citation must contain the annotated supporting span, or an annotated sufficient alternative;
- no usable answer is never correct;
- `validated` also requires every required provision to have been in the prompt.

| Run | Required provisions in prompt | Verdicts per published relationship | Citation | Correct | Validated |
|---|---|---|---|---|---|
| E01 | Art. 52 (target: whole document) | repeals → `match`, supersedes → `match` | contains gold | yes | **yes** |
| E13, first pilot | Art. 35 yes, **Art. 17 no** | none published | n/a | yes | **no**: target not in prompt |
| E13 re-run | Art. 35, Art. 17 | references 35→17 → `unscored` (allowed, not forbidden) | n/a (negative case) | yes | **yes** |
| E12 | Art. 47, Art. 8 | implements 47→8 → `match` | contains gold | yes | **yes** |
| E05 | Art. 15, Art. 14 | references 15→14 → `match` | contains gold | yes | **yes** |
| E10 | Art. 14 (2026-10), Art. 11 (2018-16) | none, **truncated** | n/a | **no** | **no** |

**Latest run per case:** 4 validated (E01, E05, E12, E13); E10 not correct. **Not run live:** E02, E03, E04, E06, E07, E08, E09, E11. That is not a benchmark, and no accuracy figure is claimed.

**E10 truncation:**

- With both circulars in scope, the prompt was the largest so far (17,860 characters, 4,511 tokens).
- The model spent 2,046 of the 2,048 completion tokens reasoning and returned no content. The partial output was never parsed.
- This is a case-specific budget exhaustion, not an API or schema failure, and it was not retried.

#### C. Historical scores (as recorded at run time; superseded)

Each file's `score` field is what the scorer **of that moment** recorded. These fields are kept unchanged for audit and **must not be quoted as current metrics**.

- **E10:** recorded as correct/validated by a scorer that treated "published nothing" as a correct abstention even without a model answer.
- **E01, first E13 pilot:** predate the `validated` field.
- **Superseded rules:** all historical scores also used the old any-character-overlap citation rule and "any correct relationship wins". Scorer v2 replaced both after the independent review.

#### What the citation check does and does not show

`contains gold` means the cited evidence, located verbatim in the source provision, contains the annotated supporting span. That is a check of *where* the support is. Whether the relation type is right is judged against the case's `acceptable_types`, which are the author's annotation, not a domain-reviewed fact. Nothing here is semantic verification, and every relationship stays a `candidate`.
