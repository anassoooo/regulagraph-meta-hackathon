# RegulaGraph backend

RegulaGraph is an AI agent that investigates the impact of public Central Bank of Tunisia (BCT) regulations. It traces how changes affect related provisions and produces impact reports in which every claim is backed by evidence.

**Status: Sprint 1 (foundation).** Done so far: the health endpoint, domain schemas, PDF extraction with page-level provenance, evidence verification and the Meta model adapter. The investigation agent, retrieval, graph storage and frontend are not built yet.

## Layout

```
app/
  main.py                  FastAPI app factory
  api/health.py            GET /health/live
  core/config.py           environment-based settings (pydantic-settings)
  core/errors.py           structured errors + FastAPI handler
  core/ids.py              deterministic, content-derived identifiers
  models/schemas.py        Document, ExtractedPage, Provision, Evidence, Inference,
                           RegulatoryRelationship, InvestigationReport
  services/document_parser.py  PDF -> per-page text (PyMuPDF)
  services/evidence.py         build/verify verbatim evidence spans (incl. whole reports)
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

Tests make no network calls and need no API key. They generate their PDFs at runtime and mock the Meta API with `httpx.MockTransport`.

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
- **No fabricated citations.** `locate_quote` accepts a quote only if it appears verbatim on the cited page. `verify_evidence` re-checks a span against the page text. `InvestigationReport` rejects any reference that does not resolve to a document, provision, evidence item or inference in the same report.
- **Deterministic IDs.** IDs are hashes of content and position. For example, `doc_<sha256[:16]>` is computed from the PDF bytes, so it does not change if the file is renamed. If a supplied ID does not match its content, it is rejected.
- **Structured errors.** Errors subclass `RegulaGraphError` (`code`, `message`, `details`) and are returned as `{"error": {...}}`.

## Meta Model API contract

Checked against the official docs on 2026-10-27 (see the docstring in `services/meta_client.py`):
`POST https://api.meta.ai/v1/chat/completions`, Bearer authentication, an OpenAI-style `messages` request, and the reply in `choices[0].message.content`. Errors come back as `{"error": {"message", "type", "param", "code"}}`. Status codes 429, 500 and 503 are flagged retryable. No live call has been made yet.

## Licensing

PyMuPDF is AGPL-3.0, and the repository does not have a licence yet. See [LICENSING.md](LICENSING.md) before any public deployment.

## Source documents

Do not commit PDFs (`*.pdf` and `data/raw/` are git-ignored). Download public BCT circulars into `data/raw/` and record where each came from in `data/manifest.json`.
