# RegulaGraph backend

RegulaGraph is an AI agent that investigates the impact of public Central Bank of Tunisia (BCT) regulations. It traces how changes affect related provisions and produces impact reports in which every claim cites a source passage.

**Status: Sprint 2 (first end-to-end investigation).** The working pipeline:

1. real BCT circulars, hash-verified;
2. provision spans with exact offsets;
3. a narrow relationship investigation in which Muse Spark proposes and deterministic validators check;
4. finalization;
5. a manually written evaluation set.

Every relationship is published as **`candidate`**: nothing is ever confirmed. **Live Muse calls so far:** 7 requests, about $0.065 in total, all HTTP 200: a smoke test plus 5 distinct cases (E01, E05, E10, E12, E13; E13 was run twice). 4 cases are validated and 1 (E10) was truncated. They are recorded in `eval/results/` and summarised in [eval/README.md](eval/README.md). Graph storage and a frontend are out of scope.

## Layout

```
app/
  core/config.py               environment-based settings (incl. ingestion and prompt limits)
  core/errors.py, core/ids.py  structured errors, canonical content-derived IDs
  models/schemas.py            Document, ExtractedDocument, Provision (+ segments), Evidence, Inference,
                               RegulatoryRelationship (+ LexicalMatch), InvestigationReport, FinalizedReport
  services/document_parser.py  PDF -> per-page text (pypdf), size/page limits, extraction provenance
  services/corpus.py           manifest of public BCT circulars; hash-checked loading
  services/quality.py          per-page extraction-quality signals
  services/provisions.py       provision segmentation (French circulars) and span verification (R1)
  services/relationships.py    evidence-in-provision and lexical checks for relationships (R2)
  services/evidence.py         verbatim evidence spans
  services/report.py           finalize_report: the only path to a publishable report
  services/meta_client.py      ModelAdapter protocol, Meta Model API adapter, ReplayAdapter
  services/investigator.py     retrieval -> Muse proposal -> validation -> finalized report
  services/evaluation.py       evaluation cases, scoring, structural probes
data/manifest.json             corpus: URLs, dates, SHA-256, extraction notes (PDFs not committed)
eval/cases.json                13 manually written cases (positive and negative)
eval/results/                  recorded evaluation runs
scripts/                       fetch_corpus, smoke_test, investigate, run_eval
docs/                          EXTRACTION_QUALITY.md, SPRINT2_REQUIREMENTS.md
```

## Setup

Requires Python 3.11+.

```bash
cd regulagraph
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # fill in locally; .env is git-ignored
python scripts/fetch_corpus.py    # downloads 5 BCT PDFs into data/raw/ and verifies SHA-256
```

## Test

```bash
pytest
```

- **No network, no key.** Tests make no network calls and need no API key. They pass on Python 3.11 and 3.13.
- **Real-corpus tests** (`test_real_corpus.py`, the gold-quote and structural-probe tests in `test_evaluation.py`) run only when the PDFs have been fetched. Otherwise they are skipped.
- **Live Meta test** (`tests/test_live_meta.py`) is skipped unless `RUN_LIVE_META=1`.
- **Mocked API.** Elsewhere the Meta API is mocked with `httpx.MockTransport` (`tests/test_investigator.py::test_end_to_end_with_mocked_meta_http_api`).

## Demo (reproducible, no paid call)

```bash
# What would be sent to the model (retrieved provisions), without sending it:
python scripts/investigate.py "Quelle circulaire la circulaire n°2026-10 abroge-t-elle ou remplace-t-elle ?" \
    --docs bct-2026-10-fr bct-2018-16-fr --show-prompt

# Full pipeline with a replayed, hand-written answer (one valid proposal, one with a paraphrased quote):
python scripts/investigate.py "Quelle circulaire la circulaire n°2026-10 abroge-t-elle ou remplace-t-elle ?" \
    --docs bct-2026-10-fr bct-2018-16-fr --replay examples/replay_e01_handwritten.json

# Validator evaluation over all 13 cases (no model):
python scripts/run_eval.py structural
```

The replay file is labelled as hand-written. Its output demonstrates the pipeline, not Muse.

## Live Muse calls (opt-in, paid)

Meta's pricing page (checked 2026-10-30) lists:

- pay-as-you-go billing, **with no free tier**;
- **no spending limit** and **no balance endpoint**;
- `muse-spark-1.3` at $1.25 per million input tokens and $4.25 per million output tokens.

Nothing here can check your credits, so every live entry point needs `META_INFERENCE_ENABLED=true`, `MODEL_API_KEY`, **and** an explicit `--confirm-paid-call`:

```bash
python scripts/smoke_test.py --confirm-paid-call                 # 1 request, <=64 tokens out (< $0.001)
python scripts/run_eval.py live --confirm-paid-call --case E01   # 1 request per named case; --case is required;
                                                                  # preflight: no call unless the case's provisions are retrieved
python scripts/run_eval.py rescore eval/results/live_*.json       # re-apply current scoring to recorded runs (no call)
python scripts/investigate.py "<question>" --docs ... --live --confirm-paid-call
```

**Per-request bounds:**

- at most `META_MAX_PROMPT_CHARS` characters of provision text, 24,000 by default (about 6-8k input tokens);
- at most `META_MAX_COMPLETION_TOKENS` tokens, 2,048 by default (raised from 1,024 after the pilot: E01 used 1,922 completion tokens, 1,374 of them reasoning), which per the API docs covers output plus reasoning;
- `temperature=0`, `reasoning_effort="low"`, and a strict JSON schema (`response_format`).

At a 2,048-token cap, one investigation costs about $0.02 or less. The two pilot calls cost about $0.012 and $0.010.

**What gets recorded:** each call stores the model ID, token usage (including reasoning tokens), latency, HTTP status, finish reason, refusal and rate-limit headers. The key is never logged or written.

## How an investigation works (`services/investigator.py`)

1. **Retrieve.** Retrieval is BM25 over the question, with bonuses for provisions that cite another corpus document and for documents the question names. Ranking is tiered: articles the question names come first, then the body articles they cite within the same circular, then everything else by score. Retrieval is bounded by provision count and character budget, and provisions are never truncated.
2. **Prompt.** Muse sees document and provision IDs and text in `<provision>` elements, which are marked as data, not instructions. It must answer in a strict JSON schema with source, type, target (a provision or a whole document), verbatim quotes and a rationale. There is **no confidence field**.
3. **Model failures.** Refusal, `finish_reason=length` (never parsed), `content_filter`, empty content, malformed JSON, provider errors and disabled inference all produce a finalized report with **no relationships**, plus a recorded outcome.
4. **Per-proposal checks.** Each proposal is rejected (with a recorded reason) if:
   - it is malformed;
   - it uses an unknown source or target ID, or a quoted provision as its source;
   - it relates something to itself;
   - it has a quote outside the source or target;
   - its quote is not found verbatim;
   - it has no source-side evidence;
   - it duplicates another proposal.
5. **Accepted proposals.** Each becomes `Inference` + `RegulatoryRelationship(basis="inferred", status="candidate")` with exact evidence spans. A `lexical_match` is recorded when the source quote names the target. That is only a lexical mention, not proof.
6. **Finalize.** `finalize_report` re-validates everything (below) and returns an immutable, digest-bound `FinalizedReport`.

## Evaluation (`eval/`)

`eval/cases.json` holds 13 cases: 7 positive and 6 negative. They cover:

- an explicit repeal;
- relationship direction and type;
- negation ("ne sont pas applicables", "sans préjudice");
- repeated article labels (Annex 5 of 2026-10 has its own Articles 1-11);
- an extraction artefact (`l’article 1 7`);
- targets outside the corpus (91-24, 2017-08);
- a provision-level overreach;
- an unrelated pair.

Gold quotes are checked automatically against the extracted text. The cases were written from the text during implementation and **have not been reviewed by a domain expert** (`review.reviewed_by` is `null`).

**Recorded results** (`eval/results/`):

- **Structural probes** (`python scripts/run_eval.py structural`, no model). Hand-made proposals go through the real pipeline. See `eval/README.md` for the recorded run. Gold answers are accepted. Paraphrased quotes, fabricated targets, reversed directions and quotes taken from unrelated provisions are rejected. Wrong relation types and forbidden links that cite a real quote **are published as candidates**, which is the measured limit of structural validation.
- **Live runs:** 5 distinct cases.
  - **Validated, 4:** E01 (repeal), E12 (implements, with direction), E05 ("sans préjudice": references) and the E13 re-run (qualified reference: references only).
  - **Not validated:** E10 was truncated, because reasoning used the whole 2,048-token budget. The first E13 run is not validated either, since its target was not in the prompt.
  - Details are in `eval/README.md`. This is not a benchmark, and no accuracy is claimed.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MODEL_API_KEY` | unset | Meta Model API key, sent as `Authorization: Bearer …`. `SecretStr`: never shown in reprs or logs |
| `META_INFERENCE_ENABLED` | `false` | **Billing safeguard.** No request is sent unless this is `true` |
| `META_API_BASE_URL` | `https://api.meta.ai/v1` | API base URL |
| `META_MODEL_ID` | `muse-spark-1.3` | Model identifier |
| `META_MAX_COMPLETION_TOKENS` | `2048` | Upper limit on `max_completion_tokens` (output plus reasoning) for every request; 1-32768. `run_eval.py --max-completion-tokens` overrides it per run |
| `META_MAX_PROMPT_CHARS` | `24000` | Upper limit on provision text in one prompt |
| `META_REQUEST_TIMEOUT_SECONDS` | `60` | HTTP timeout |
| `MAX_PDF_BYTES` | `20000000` | Ingestion limit, checked before the file is read or parsed |
| `MAX_PDF_PAGES` | `200` | Ingestion limit, checked before any text is extracted |
| `LOG_LEVEL` | `INFO` | Logging level |

## Design rules

- **Evidence vs inference.** `Evidence` is a verbatim span of extracted page text: document, 1-based page number, character offsets and the quote. Models never produce it; quotes from a model are *located* in the source, and the span comes from the source. `Inference` is model output, naming its `model_id` and citing evidence.
- **Provision identity is the source span (R1).** `Provision` is identified by `(document_id, segments)`, where each segment is `(page, char_start, char_end)` and one segment covers each consecutive page. The label (`Article 5 :`) and normalised `number` are display and lookup attributes, so repeated labels never collide.
  - **Text** is the segment texts joined by an explicit `PROVISION_SEGMENT_SEPARATOR` (`"\n"`).
  - **Spans keep the source exactly,** including running headers and page numbers.
  - **Nesting is recorded** (`parent_provision_id`) for an annex's own articles and for quoted headings (`quoted=True`). A quoted provision can never be a relationship source.
- **Relationship evidence must sit in the related provisions (R2).** At finalization, every cited evidence item must lie inside one segment of the source or target provision, and at least one inside the source. An inference may cite only the relationship's evidence. A recorded `lexical_match` must point into source-side evidence and match the target's designation (`regulagraph.lexical.v1`). `basis="extracted"` requires such a match.
- **Mandatory finalization.** `finalize_report(report, sources)` works in this order:
  1. Re-validates a serialized snapshot of the report.
  2. Re-validates each source extraction.
  3. Applies the fail-closed publication rules.
  4. Verifies every quote, then every provision span (and the overlap and nesting rule), then the R2 checks.
  5. Returns an immutable `FinalizedReport` (canonical snapshot, `content_digest`, read-only `verified_sources`).
- **Fail-closed publication.** `status` defaults to `"candidate"`. `finalize_report` and `FinalizedReport` reject every `"confirmed"` relationship, because no confirmation procedure exists. A model can never set the status: its schema has no such field.
- **Deterministic IDs.** IDs are canonical-JSON hashes of content and position (128 bits; content digests 256 bits). Field values containing separators or look-alike types cannot collide. `report_id` identifies the run; `content_digest()` binds the findings.
- **Extraction provenance.** Each extraction records the extractor name, version and mode, plus a `text_digest`. Corpus loading checks the PDF bytes against the manifest SHA-256 before parsing. The text digest is tamper-evident but does **not** authenticate text against PDF bytes: only re-extraction does.
- **Structured errors.** `RegulaGraphError` (`code`, `message`, `details`) is returned as `{"error": {...}}`. Limit errors name the limit.

> **Scope of verification.** The checks prove that a cited passage exists at the stated location, lies inside the provision it is attributed to, and (when recorded) names the target. They do **not** prove that the passage means what the relationship says. "Sans préjudice de l'article 14" and "abroge l'article 14" pass the same structural checks. Semantic entailment is not implemented, and neither a lexical match nor anything the model says is treated as proof. `tests/test_relationships.py::test_lexical_match_is_not_semantic_support` and the structural probes keep this limit visible.

## Known limitations

- **Live evidence is thin.** 5 of 13 cases have been run live. Reasoning can exhaust the 2,048-token budget on large two-document prompts (E10).
- **Semantic errors pass validation.** A wrong relation type, or a link to the wrong but real target, is published as a candidate. Review is required before any use.
- **Retrieval is lexical** (BM25 plus bonuses) and may miss the relevant provision. The evaluation records retrieval hits per case.
- **Segmentation fits BCT French layouts only.** Headings must start a line and carry a separator. Annexes are recognised only at the top of a page. Replacement text for another instrument's annex is not marked as quoted.
- **Arabic is not used.** The Arabic 2018-16 PDF extracts with substituted digits and letters, and no automatic signal detects it (see `docs/EXTRACTION_QUALITY.md`).
- **Not bound yet:** evidence is not bound to an extraction digest, and stored artifacts are not re-extracted (R3 remainder). There is no extraction time limit or decompression-bomb guard (R5 remainder).
- **No OCR.**
- **Reuse rights.** The PDFs are not redistributed. The BCT site terms do not clearly allow it.

## PDF extraction

[pypdf](https://pypi.org/project/pypdf/) (BSD-3-Clause) extracts text page by page with `extract_text(extraction_mode="plain")`, stored unchanged; evidence offsets index into it. Measured behaviour on the real corpus is in [docs/EXTRACTION_QUALITY.md](docs/EXTRACTION_QUALITY.md). pypdf is pinned to `<7`.

## Meta Model API contract

The contract was verified on 2026-10-27 and re-checked on 2026-10-30. See the docstring in `services/meta_client.py`:

- **Endpoint and auth:** `POST https://api.meta.ai/v1/chat/completions`, with Bearer authentication.
- **Request fields used:** `response_format` (`json_schema`), `reasoning_effort`, and `max_completion_tokens`, which covers output plus reasoning.
- **Response:** the reply is in `choices[0].message.content`, alongside `refusal` and `finish_reason` (`stop`, `length`, `content_filter`, …).
- **Errors:** they come back as `{"error": {...}}`; 429, 500 and 503 are retryable.

## Licensing

All runtime dependencies are permissively licensed. Choosing a licence for the project itself is still an open decision. See [LICENSING.md](LICENSING.md).
