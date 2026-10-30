# RegulaGraph backend

RegulaGraph is an AI agent that investigates the impact of public Central Bank of Tunisia (BCT) regulations. It traces how changes affect related provisions and produces impact reports in which every claim cites a source passage.

**Status: Sprint 1 (foundation).** Done so far: the health endpoint, domain schemas, PDF extraction with page-level provenance, evidence verification and the Meta model adapter. The investigation agent, retrieval, graph storage and frontend are not built yet.

## Layout

```
app/
  main.py                  FastAPI app factory
  api/health.py            GET /health/live
  core/config.py           environment-based settings (pydantic-settings)
  core/errors.py           structured errors + FastAPI handler
  core/ids.py              deterministic, content-derived identifiers
  models/schemas.py        Document, ExtractedDocument (+ ExtractionProvenance), Provision,
                           Evidence, Inference, RegulatoryRelationship, InvestigationReport,
                           FinalizedReport
  services/document_parser.py  PDF -> per-page text (pypdf), with extraction provenance
  services/evidence.py         build/verify verbatim evidence spans (incl. whole reports)
  services/report.py           finalize_report: the only path to a publishable report
  services/meta_client.py      ModelAdapter protocol + Meta Model API adapter
tests/                     pytest suite (synthetic PDFs, mocked HTTP)
```

## Setup

Requires Python 3.11+.

```bash
cd regulagraph
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # then fill in values locally; .env is git-ignored
```

## Run

```bash
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health/live    # {"status":"ok"}
```

## Test

```bash
pytest
```

Tests make no network calls and need no API key. They pass on Python 3.11 and 3.13. They generate their PDFs at runtime and mock the Meta API with `httpx.MockTransport`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MODEL_API_KEY` | unset | Meta Model API key, sent as `Authorization: Bearer …`. Stored as `SecretStr`, so it is never shown in reprs or logs. |
| `META_INFERENCE_ENABLED` | `false` | **Billing safeguard.** The adapter refuses to send any request unless this is `true`. |
| `META_API_BASE_URL` | `https://api.meta.ai/v1` | API base URL |
| `META_MODEL_ID` | `muse-spark-1.3` | Model identifier |
| `META_MAX_COMPLETION_TOKENS` | `1024` | Upper limit on `max_completion_tokens` for every request |
| `META_REQUEST_TIMEOUT_SECONDS` | `60` | HTTP timeout |
| `LOG_LEVEL` | `INFO` | Logging level |

## Design rules

- **Evidence vs inference.** `Evidence` is a verbatim span of extracted page text: document, 1-based page number, character offsets and the quote. Models never produce it. `Inference` is model output; it must name its `model_id` and cite at least one evidence ID. A `RegulatoryRelationship` has `basis` set to `extracted` (stated in the text) or `inferred` (needs an `inference_id`). Both kinds need evidence.
- **Quotes must exist in the source.** `locate_quote` accepts a quote only if it appears verbatim on the cited page. `verify_evidence` and `verify_report_evidence` re-check spans against the extracted page text. `InvestigationReport` rejects any reference that does not resolve to a document, provision, evidence item or inference in the same report.

> **Scope of verification.** These checks prove that a cited passage **exists** at the stated location. They do **not** prove that the passage semantically supports the inference or relationship citing it. They also don't check that the passage belongs to the provisions a relationship names. A real quote attached to an unrelated claim passes every current check. Neither semantic support checking nor checking evidence against provisions is implemented. Both are planned for Sprint 2 (see [docs/SPRINT2_REQUIREMENTS.md](docs/SPRINT2_REQUIREMENTS.md)).
- **Deterministic IDs.** IDs are hashes of content and position. For example, `doc_<sha256[:32]>` is computed from the PDF bytes, so it does not change if the file is renamed. If a supplied ID does not match its content, it is rejected.
  - **No delimiter joining.** ID inputs are hashed as canonical JSON (`ids.stable_id`: scheme tag, prefix, typed parts, nested lists kept as lists). Field values containing separators, JSON syntax or look-alike types (`1` vs `"1"`) cannot collide.
  - **Length.** IDs keep 128 bits of SHA-256. Content digests use all 256 bits.
  - **Document IDs** must match `doc_<32 hex>`, which keeps `page_id` (`<doc_id>:p<n>`) unambiguous.
- **Run ID vs content digest.** `InvestigationReport.report_id` identifies the investigation *run*: question, time and documents. It does not cover findings, so two runs with the same inputs and different findings share it. `content_digest()` is a full SHA-256 over the report's canonical JSON, so any change to a finding changes it.
- **Mandatory finalization.** Pydantic doesn't re-validate on attribute assignment or list mutation, so a report can be changed after it was built. `services/report.py:finalize_report(report, sources)` is the only supported way to publish. It works in this order:
  1. Re-validates a serialized snapshot of the report.
  2. Re-validates each source extraction.
  3. Applies the publication rules.
  4. Verifies every quote against its source page text.
  5. Returns an immutable `FinalizedReport`: canonical snapshot text, `content_digest`, and the extraction digests it was verified against (`verified_sources`, a read-only mapping copied at construction, so it can't be edited in place). Loading a `FinalizedReport` re-checks the digest, the canonical form and the publication rules. It cannot re-check evidence without the sources.
- **Fail-closed publication.** `RegulatoryRelationship.status` defaults to `"candidate"`, meaning proposed and not confirmed. Only `basis="extracted"` relationships may ever be `"confirmed"`. Confirmation checks don't exist yet (provision association is Sprint 2 R2), so `finalize_report` and `FinalizedReport` **reject every confirmed relationship**. The future investigator can publish only candidates until those checks exist. Consumers must present candidates as unconfirmed.
- **Extraction provenance.** Each `ExtractedDocument` records `extractor_name`, `extractor_version`, `extraction_mode` and a `text_digest`, a SHA-256 over the source PDF hash and every page's text. Validation recomputes the digest, so a stored extraction artifact is tamper-evident, and a re-extraction can be compared exactly.
  > The digest does **not** authenticate the text against the PDF bytes. Anyone who alters the text can recompute the digest. Only re-running the recorded extractor on the source PDF proves where the text came from. A test pins this limit.
- **Structured errors.** Errors subclass `RegulaGraphError` (`code`, `message`, `details`) and are returned as `{"error": {...}}`.

## PDF extraction

[pypdf](https://pypi.org/project/pypdf/) (BSD-3-Clause) extracts text page by page with `extract_text(extraction_mode="plain")`. RegulaGraph stores that output unchanged. Accents, non-breaking spaces, repeated spaces, tabs, line breaks and hyphenation are kept, and tests pin this behaviour. Evidence offsets index into this stored text.

How it differs from the PyMuPDF version (Sprint 1, commit `2b6bd13`):

| Aspect | PyMuPDF 1.28 | pypdf 6.x |
|---|---|---|
| End of page text | Appended a trailing `\n` | No trailing newline |
| Spacing and line breaks | MuPDF's text-line reconstruction | pypdf's own heuristics from text-positioning operators. On real PDFs (justified text, kerning) the spaces and breaks **may differ**; this has not been measured on BCT documents yet |
| Reading order | Content-stream order | Content-stream order. Neither reorders multi-column layouts |
| Encrypted PDFs | Opened files with only an owner password; rejected files needing a user password | Same behaviour. AES needs `cryptography`, installed through the `pypdf[crypto]` extra |
| Damaged files | MuPDF repair | Lenient parsing (`strict=False`) logs warnings through the `pypdf` logger and may partly recover. Parse failures become `PDFExtractionError` |
| Speed | Native C | Pure Python, expected to be slower (not benchmarked) |

Document IDs are unchanged because they hash the PDF bytes. Page text and evidence offsets from the old parser are **not** compatible, but no extracted data had been stored yet. Extracted text also depends on the parser version, so pypdf is pinned to `<7`, and every extraction records the extractor version (see *Extraction provenance* above).

## Meta Model API contract

Checked against the official docs on 2026-10-27 (see the docstring in `services/meta_client.py`):
`POST https://api.meta.ai/v1/chat/completions`, Bearer authentication, an OpenAI-style `messages` request, and the reply in `choices[0].message.content`. Errors come back as `{"error": {"message", "type", "param", "code"}}`. Status codes 429, 500 and 503 are flagged retryable. No live call has been made yet.

## Licensing

All runtime dependencies are permissively licensed. Choosing a licence for the project itself is still an open decision. See [LICENSING.md](LICENSING.md).

## Source documents

Do not commit PDFs (`*.pdf` and `data/raw/` are git-ignored). Download public BCT circulars into `data/raw/` and record where each came from in `data/manifest.json`.
