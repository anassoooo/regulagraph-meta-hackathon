# RegulaGraph backend

RegulaGraph is an AI agent that investigates the impact of public Central Bank of Tunisia (BCT) regulations. It traces how changes affect related provisions and produces impact reports in which every claim cites a source passage.

**Status: Sprint 3.5 (local demo: replay by default, opt-in live mode with review).** A web demo shows recorded Muse investigations, with relationships drawn as a graph and exact source evidence. See [Web demo](#web-demo-sprint-3-replay-only). An opt-in local live mode runs new questions over the documents you approve, and records reviewer decisions. See [Live mode](#live-mode-with-review-sprint-35-local-only-paid-calls). The pipeline from Sprint 2 underneath:

1. real BCT circulars, hash-verified;
2. provision spans with exact offsets;
3. a narrow relationship investigation in which Muse Spark proposes and deterministic validators check;
4. finalization;
5. a manually written evaluation set.

Every relationship is published as **`candidate`**: nothing is ever confirmed. **Live Muse calls so far:** 7 requests, about $0.065 in total, all HTTP 200: a smoke test plus 5 distinct cases (E01, E05, E10, E12, E13; E13 was run twice). Under the current scorer (v2), 4 cases are validated and 1 (E10) was truncated. Historical per-run scores are kept separately and superseded. They are recorded in `eval/results/` and summarised in [eval/README.md](eval/README.md). There is no graph database and no deployment.

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
  services/replay.py           deterministic replay of recorded Muse output through the real pipeline
  api/investigations.py        replay-only demo API (/api/scenarios, /api/scenarios/{id}/replay, /api/evaluation)
  web/                         static client: index.html, app.js (DOM), view.js (pure logic), style.css
data/manifest.json             corpus: URLs, dates, SHA-256, extraction notes (PDFs not committed)
eval/cases.json                13 manually written cases (positive and negative)
eval/results/                  recorded evaluation runs
scripts/                       fetch_corpus, demo, smoke_test, investigate, run_eval
tests/js/                      node --test unit tests for the client view logic, journey helper
docs/                          EXTRACTION_QUALITY.md, SPRINT2_REQUIREMENTS.md, REVIEW_HANDOFF_SPRINT2/3.md
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
- **Web client.** `node --test tests/js/view.test.mjs tests/js/controller.test.mjs` runs the client tests: the view logic, and the real `app.js` controller against a minimal fake DOM with controlled response ordering (latest scenario selection wins). `pytest` also runs them, plus the E01 journey test, when Node is installed.

## Web demo (Sprint 3, replay only)

```bash
python scripts/fetch_corpus.py   # in advance: downloads the BCT PDFs, verifies SHA-256 (git-ignored)
python scripts/demo.py           # preflight, then http://127.0.0.1:8000   (--check: preflight only)
```

**Preflight** (`scripts/demo.py`) refuses to start, with an actionable message, if:

- a corpus PDF is missing or fails its SHA-256 check;
- a scenario's recording is missing, malformed, or inconsistent with the scenario (case ID, question, recorded model, prompt provisions, report metadata);
- any scenario's replay does not reproduce its recording exactly (content digest, report ID, prompt), does not publish the expected candidate relation types, or the current scorer run on the replay disagrees with the stored evaluation;
- the port is taken.

It also starts the server with `META_INFERENCE_ENABLED=false`.

**Demo script (about 2 minutes):**

1. Note the permanent **REPLAY MODE** banner. No model is called. Live endpoints refuse every request unless the server was started with `--live`.
2. Click **E01 · primary**. The question is "Quelle circulaire la circulaire n°2026-10 abroge-t-elle ou remplace-t-elle ?". The graph shows **Article 52 (2026-10)** with two dashed AI-candidate edges, **repeals** and **supersedes**, pointing to **circular 2018-16 (whole circular)**.
3. Click either edge (or Tab to it and press Enter). The evidence panel shows:
   - the badges *AI-generated · Candidate · Unconfirmed*;
   - the **verified source text**: the exact quotation at page 33, characters 1158–1428, with a link to the official BCT PDF at that page;
   - the model's **interpretation**, labelled *not verified*.
4. Optionally run E12 (implements 47 → 8), E05 ("sans préjudice", a reference) and E13 ("ne sont pas applicables", a reference only).
5. Open **Evaluation**: scorer v2 results, with **E10 shown as truncated (no answer)**, cases not run live marked as such, and the 15 semantic errors that structural checks cannot detect.

**Replay provenance.** A replay is not a stored screenshot of a result. `app/services/replay.py` takes the raw Muse output recorded during an authorised live run (`eval/results/live_*.json`, listed in `current_scoring_summary.json`). It feeds that output, via `ReplayAdapter`, back through the real pipeline (`investigate` → `finalize_report`), with the same prompt provisions and the recorded timestamp. Every quotation is re-located in the hash-verified PDF text on each replay, and every scenario must reproduce its recorded run's content digest exactly. The evaluation returned with a replay is recomputed by the current scorer from that replay; stored labels are used only as a cross-check and never shown on their own. If the PDFs, the recordings, the provision IDs or any recording metadata no longer match, the API returns 503 and shows nothing.

**What the screen does and does not claim.**

- *Verified source text* means the quotation exists at those offsets inside the cited provision. It does not mean the relationship is legally correct.
- Every relationship is an AI-generated **candidate** and **unconfirmed**. No control can confirm one.
- *Validated* in the evaluation view means the recorded answer matched the author's annotation. Neither the annotations nor the relationships have been reviewed by a domain expert.

**Content shown (PM decision D3).** Exact evidence quotations, provision labels, page numbers, character offsets and official BCT PDF links only. The API never returns full article text, and no PDFs or extracted text are committed.

**Security.**

- **Scenarios:** curated only, with no free text and no uploads.
- **Rendering:** untrusted text is inserted with `textContent` only.
- **Headers:** a strict CSP (`default-src 'none'; script-src 'self'; style-src 'self'; …`) with no inline code and no CDN, plus `nosniff`, `no-referrer`, frame denial and `no-store`.
- **Redaction:** responses are scrubbed of credentials.
- **No API docs:** the FastAPI docs and OpenAPI routes are disabled.

## Live mode with review (Sprint 3.5, local only, paid calls)

```bash
python scripts/demo.py --live --confirm-paid-calls                  # http://127.0.0.1:8000, budget 4.50 USD
python scripts/demo.py --live --confirm-paid-calls --budget-usd 1   # lower cumulative ceiling (max 5.00)
```

Needs `MODEL_API_KEY` in `.env`. Without `--live`, the demo stays replay-only, and every `/api/live` call except `status` returns 403.

**What it does.** The "Live investigation" panel takes a free-text question and the source documents you tick: any of the three French circulars, none ticked by default. It then makes one real Muse call, capped at 4,096 output tokens; replay settings keep 2,048. The result goes through the same `investigate()` → `finalize_report()` pipeline as a replay: quotations are re-located in the hash-verified PDF text, and every relationship is an unconfirmed AI candidate. For each relationship, a reviewer can record **accept**, **reject** or **needs more evidence**, with an optional note.

**Guarantees (`app/services/live.py`, `app/api/live.py`).**

- **Allowlist.** Only the approved documents are passed to retrieval and the prompt. Before the call, the prompt's *source context* is checked to contain only approved documents and provisions. The question itself is the user's text and may mention any circular. After the call, the report is checked to reference only approved documents. A model answer that targets an excluded document is rejected (`unknown_target`).
- **Spending.** One call at a time. Before each call the worst case is reserved in `var/live/ledger.jsonl`, an append-only, hash-chained ledger: prompt UTF-8 bytes as input tokens, plus the 4,096-token live output cap. The call is refused if it could pass the cumulative ceiling or the call cap (40). Afterwards the reservation is settled at the cost of the reported usage. Costs are estimates from published prices, not invoices. If the ledger is modified, all calls are blocked.
- **Requests.** Accepted only from a loopback client with a `127.0.0.1` or `localhost` Host header. POSTs must also be JSON with this server's `Origin`, not marked cross-site, and include `"confirm_paid_call": true`. The UI clears its confirmation box after every call.
- **Reviews never change reports.** A live run is written once to `var/live/runs/<report_id>.json` and never rewritten. A review is a separate hash-chained record in `var/live/reviews.jsonl`, bound to the run's `report_id`, `content_digest` and `relationship_id`. *Reviewer accepted* is a reviewer's opinion, not a legal confirmation: the relationship stays an unconfirmed AI candidate.
- **Replay is unchanged.** `tests/test_replay_golden.py` pins the byte-exact responses of the four replays, in both replay-only and live mode.

`var/` is git-ignored.

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
- **Credential redaction** (`app/core/redaction.py`). `SecretStr` alone is not relied on. The configured key and any `Bearer <token>` are replaced with `[REDACTED]` in four places:
  - at the provider error boundary, because a provider may echo the key;
  - in every model-call record and parsed model output;
  - in the final output of every logging handler (message, `extra` fields, tracebacks including chained exceptions, stack info), at the `logging.Handler.format` boundary, once `install_log_redaction` has run (the app and all scripts call it);
  - in every file the scripts write.

  `tests/test_redaction.py` echoes a dummy key from a mocked provider and checks it never appears in the outcome, the logs or a saved result.

> **Scope of verification.** The checks prove that a cited passage exists at the stated location, lies inside the provision it is attributed to, and (when recorded) names the target. They do **not** prove that the passage means what the relationship says. "Sans préjudice de l'article 14" and "abroge l'article 14" pass the same structural checks. Semantic entailment is not implemented, and neither a lexical match nor anything the model says is treated as proof. `tests/test_relationships.py::test_lexical_match_is_not_semantic_support` and the structural probes keep this limit visible.

## Known limitations

- **Demo is local.** By default it shows four recorded investigations. New questions need the opt-in live mode: paid, loopback only, over the three French circulars. There is no deployment. On phones the graph scrolls sideways inside its box, and the relationship list below it carries the same information.

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
