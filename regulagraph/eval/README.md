# Evaluation

`cases.json` holds 12 manually written relationship cases over the French corpus in `data/manifest.json`: 7 positive and 5 negative.

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

Command: `python scripts/run_eval.py structural`. Recorded run: `results/structural_20261030T135811Z.json`. It used corpus PDFs fetched on 2026-10-30, pypdf 6.21.2, and the default `META_MAX_PROMPT_CHARS=24000`.

The probes are hand-made proposals replayed through the real pipeline (`investigate` → `finalize_report`). They measure **what validation does**, not what Muse does.

| Probe | Expected | Observed |
|---|---|---|
| Gold answer (7 positive cases) | published | 7 / 7 published |
| Paraphrased quote | rejected | 7 / 7 rejected (`quote_not_found`) |
| Fabricated target document ID | rejected | 7 / 7 rejected (`unknown_target`) |
| Reversed direction (provision targets) | rejected | 5 / 5 rejected (`no_source_evidence`) |
| Extra quote from an unrelated provision | rejected | 5 / 5 rejected (`quote_outside_related`) |
| Wrong relation type with the gold quote | *cannot be detected* | 7 / 7 **published as candidates** |
| Forbidden link backed by a real quote (negative cases) | *cannot be detected* | 7 / 7 **published as candidates** |

The last two rows are the measured limit of structural validation. When a semantically wrong relationship cites a real passage from its own source provision, it passes every check and is published as a `candidate`. Only the model's judgement, or a human reviewer, stands between that proposal and the report.

Retrieval (deterministic BM25 plus bonuses, case question only, documents scoped per case):

| Case | Gold source retrieved | Gold target retrieved |
|---|---|---|
| E01, E03 | yes | (whole document) |
| E04, E05, E06, E07 | yes | yes |
| E12 | yes | **no**: Article 8 is not retrieved for "En application de quel article … l'article 47 …" |

Gold source: 7 of 7. Gold provision target: 4 of 5. In E12, Muse cannot propose the correct target because it never sees it.

### Live Muse evaluation: not run

No live run has been made. A run needs `META_INFERENCE_ENABLED=true`, `MODEL_API_KEY` and confirmed credits:

```
python scripts/run_eval.py live --confirm-paid-call
```

It makes one request per case, 12 in all, each within the prompt and token bounds in the README. It records:

- per-case correctness, type, direction and target errors, and citation overlap with the gold quote;
- model outcome, token usage and latency;
- the full finalized report.

These are written as `results/live_<timestamp>.json`. No model results are claimed until such a file exists.
