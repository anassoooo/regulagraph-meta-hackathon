# RegulaGraph

**RegulaGraph checks what an AI model says about Tunisian banking regulations against the official text.**
Meta Muse proposes how circulars of the Banque Centrale de Tunisie (BCT) relate to each other: which article repeals, replaces, amends, implements or only cites another. RegulaGraph publishes a proposal only if every quotation it relies on is found, character for character, in the cited provision of the official PDF. Everything published stays an unconfirmed candidate for a human reviewer.

Built for the Meta Global AI Developer Hackathon (2026). The application lives in [`regulagraph/`](regulagraph/README.md), which has the full technical documentation.

## Why

In September 2026 the BCT published circular 2026-10 on payment institutions. Its Article 52 reads: "La présente circulaire abroge et remplace la circulaire n°2018-16". A compliance team needs to know which provisions changed, which were replaced and which are only cited. A language model can answer that fluently, but it can also:

- paraphrase a quotation, or invent one;
- cite an article that does not exist;
- read a qualified reference ("sans préjudice de", "ne sont pas applicables") as a repeal.

In regulatory work, an answer that cannot be traced to the exact source text is not usable.

## What it does today

1. **Source:** five public BCT PDFs, each checked against a recorded SHA-256 before use. The three French circulars are used for investigation; the Arabic versions are not (one is unusable after extraction).
2. **Retrieval:** deterministic BM25 with fixed bonuses selects at most 8 provisions (articles), up to 24,000 characters.
3. **Proposal:** a single Meta Muse call, constrained by a strict JSON schema, proposes relationships between those provisions. Each proposal must include its quotations.
4. **Validation by code:**
   - every quotation must be found verbatim inside the provision it cites;
   - IDs must be ones the model was shown;
   - the source provision must contain supporting text;
   - quotes from unrelated provisions are rejected.
   One failed check rejects the whole proposal.
5. **Publication:** what survives is published as an **AI-generated, unconfirmed candidate**, with the quotation, page, character offsets and a link to the official PDF. Nothing in the system can mark a relationship as confirmed.

**Three things kept separate:**

| | Checked by | What it means |
|---|---|---|
| **Source fidelity** | Code, whenever an answer is produced or replayed | The quotation exists at these offsets in the cited provision. It proves where the words are, not what they mean. |
| **Semantic correctness** | Nobody automatically | In a validation test, 15 of 15 hand-authored proposals with a wrong relation type, or a forbidden link backed by a real quotation, passed every structural check. |
| **Human review** | A reviewer (local live mode) | Accept, reject or "needs more evidence", stored separately from the report. A reviewer's opinion, never a legal confirmation. |

**What it is not:**
- not an autonomous agent: one model call per question, with no multi-step planning or tool use;
- not corpus-wide impact tracing: each investigation covers the provisions retrieved for one question;
- not legal advice, and not a benchmark (see Evaluation).

## Meta Muse integration

- **Model:** `muse-spark-1.3` through the Meta Model API (`/v1/chat/completions`).
- **Structured output:** `response_format` with a strict JSON schema. The schema has no confidence field, so the model cannot express certainty that the system might mistake for proof.
- **Settings:** `reasoning_effort=low`; output capped at 2,048 tokens for the recorded runs, and at 4,096 in local live mode.
- **Usage so far:** 7 requests, about **$0.065** in total (estimated from published prices), about one cent per investigation. One run (E10) used 2,046 of its 2,048 tokens on reasoning and returned no answer. It is reported as truncated, not retried.
- **Replays:** the demo shows recorded Muse answers. Locally, each replay runs the recorded output through the real pipeline again and re-locates every quotation in the PDF text.

## Try it

| Mode | Where | Model calls | Evidence check |
|---|---|---|---|
| **Hosted static snapshot** | Link to be added after PM approval | None | Done once, when the snapshot was built. The page says so, with the build commit and date. |
| **Local replay** (default) | `python scripts/demo.py` | None | On every replay, against the local PDFs |
| **Local live mode** | `python scripts/demo.py --live --confirm-paid-calls` | One paid call per run, with budget guard | On every run |

### Local live app vs. static snapshot

| | Local live app (`demo.py --live`) | Static snapshot (hosted) |
|---|---|---|
| Runs | FastAPI server on `127.0.0.1` only | Static files on a CDN, no server code |
| Needs | Python, the BCT PDFs (fetched by you), a Meta API key | A browser |
| Model calls | One paid Muse call per "Run live investigation", after an explicit confirmation | Never |
| Questions | Any question over the circulars you tick | The 4 curated scenarios only |
| Quotation check | Re-located in the PDF text on every replay and live run | Done once, when the snapshot was built; the banner shows the build commit and date |
| Human review | Accept, reject or "needs more evidence", stored locally in a hash-chained log | Not available |
| Budget guard | Worst-case reservation per call, a cumulative ceiling (default $4.50, max $5.00) and at most 40 calls | Not applicable |

The snapshot contains the page, the recorded API responses and short exact quotations (as extracted, see below), with links to the official PDFs. It contains no PDFs, no full provision text, no key and no server code.

### Run locally

Requires Python 3.11+. Node.js is optional; it runs the web client tests.

```bash
cd regulagraph
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python scripts/fetch_corpus.py    # downloads the 5 BCT PDFs and checks their SHA-256
python scripts/demo.py            # preflight, then http://127.0.0.1:8000 (replay only, no key needed)
pytest                            # no network, no key
```

### Meta API key and live mode (optional, paid)

Replay, tests and the snapshot need no key. To run new questions against Muse:

1. **Get a key.** Create one in the Meta Model API developer dashboard (API keys). Check that your credits are active there: the API has no balance endpoint, so RegulaGraph cannot check them for you.
2. **Configure it locally.** The key is never committed (`.env` is git-ignored) and never printed or logged:
   ```bash
   cd regulagraph
   cp .env.example .env
   # edit .env and set:  MODEL_API_KEY=<your key>
   # keep META_INFERENCE_ENABLED=false in .env: the scripts below enable inference only when asked
   ```
   The other defaults in `.env.example` are what the recorded runs used: `META_API_BASE_URL=https://api.meta.ai/v1` and `META_MODEL_ID=muse-spark-1.3`.
3. **Optional connectivity check.** One request of about 60 input tokens and at most 64 output tokens, under $0.001:
   ```bash
   META_INFERENCE_ENABLED=true python scripts/smoke_test.py --confirm-paid-call
   ```
4. **Start live mode.** It binds to `127.0.0.1` only:
   ```bash
   python scripts/demo.py --live --confirm-paid-calls                  # budget ceiling 4.50 USD
   python scripts/demo.py --live --confirm-paid-calls --budget-usd 1   # lower ceiling (max 5.00)
   ```
   Open http://127.0.0.1:8000, tick the source circulars, type a question, tick "I confirm one paid Meta call" and run. Each run is one Muse call capped at 4,096 output tokens. Spending is estimated from published prices and recorded in `var/live/ledger.jsonl` (git-ignored). This is a local guard, not an account spending cap.

**Demo path (about 2 minutes):**

1. The page opens on scenario E01, "Which circular does circular 2026-10 repeal or replace?". The graph shows two candidate edges, `repeals` and `supersedes`, from Article 52 to circular 2018-16.
2. The evidence panel shows the exact quotation, its page and character offsets, and a link to the official PDF page, plus the model's interpretation labelled *not verified*.
3. Try E12, E05 and E13. E13 is a qualified reference that must stay a reference.
4. Open **Evaluation** to see:
   - what the validators reject;
   - the 15 semantically wrong proposals that they cannot catch;
   - the cases that were never run live.

## What was built during the hackathon

The hackathon page lists the kickoff on 2026-10-26 and submissions due 2026-11-04, 9:00 PM ET. The build window we were given is **2026-10-27 to 2026-11-04**.

Times below are git author dates (set by the committer's clock), corroborated where possible by GitHub pull-request timestamps. Eligibility is for the organisers to decide; this section only states the facts.

| When (UTC) | Same moment in US Eastern (EDT) | Commits | Content |
|---|---|---|---|
| 2026-10-26 21:43–23:47 | 2026-10-26 17:43–19:47 | `c6b9cc5`, `76648f8`, `f9e0ccc`, `8ffe7b3`, `cb20c97`, `c3e2901` (PRs #1, #2) | **Scaffolding only:** a one-line README, empty package layout (14 lines, two commits by the GitHub Copilot coding agent) and `.gitkeep` directories. No functionality. |
| 2026-10-27 00:34–01:07 | **2026-10-26 20:34–21:07** | `2b6bd13`, `b5ac759`, `ff4d963` (PR #3, opened 2026-10-27 00:35 UTC) | Sprint 1 foundation: data schemas, PDF text extraction, the Meta Model API client and integrity checks. **Ambiguous timing:** this is 2026-10-27 in UTC and in Central European time (the author's local time, 01:34–02:07), but 2026-10-26 in US Eastern time. |
| 2026-10-30 to 2026-11-01 | same days | all later commits (PRs #3–#9) | Everything else: the real BCT corpus and provision spans, retrieval, the Muse investigation and validators, the evaluation set, all live Muse calls (2026-10-30), the web demo, live mode with human review, the UI work, the static snapshot and this remediation. |

## Evaluation and its limits

- **Cases:** 13 relationship cases (7 positive, 6 negative), written by the author from the extracted text. They have **not been reviewed by a domain expert**.
- **Live results:** 5 cases have been run live against Muse. Under the current scorer, 4 are *validated* (the answer matched the author's annotation, with every required provision in the prompt) and 1 is truncated. 8 cases have **never been run live**.
- **No accuracy claim:** the sample is far too small, and the retrieval bonuses were adjusted after analysing two of these cases.
- **Structural validation test (hand-authored proposals, no model):**

  | Hand-authored proposal | Outcome |
  |---|---|
  | Paraphrased quotation | 7 of 7 rejected |
  | Invented target | 7 of 7 rejected |
  | Reversed direction | 5 of 5 rejected |
  | Extra quotation from an unrelated provision | 5 of 5 rejected |
  | Wrong relation type with a real quotation | 7 of 7 **published as candidates** |
  | Forbidden link backed by a real quotation | 8 of 8 **published as candidates** |

  These probes are not Muse output. None of the recorded Muse runs contained a proposal that the validators rejected.

Details: [`regulagraph/eval/README.md`](regulagraph/eval/README.md).

## Source documents and licensing

- **BCT documents:** the PDFs are the BCT's and are **not redistributed**: they are downloaded from bct.gov.tn and checked by hash. The BCT site terms allow downloads for personal purposes, exact quotation with the site cited as source, and links that name the site and open in a new window. RegulaGraph shows only short quotations, with their page and a link to the official PDF ([manifest](regulagraph/data/manifest.json)). Whether the terms' exemption for official regulatory texts applies has not been confirmed, and nothing here is legal advice.
- **Quotations are exact as extracted, not as printed.** They come from the PDF's text layer, which can differ visually from the printed page: line breaks, extra spaces, and words split by a space (E12 shows "le s dossiers" where the page reads "les dossiers"). The app says so under every quotation, and the official PDF is the reference.
- **Correction:** from 2026-10-30 to 2026-11-01 the recorded evaluation runs also contained full extracted provision text (about 47% of circular 2026-10). It is removed from the current files, and a test now prevents it from returning. It remains in git history: see [`regulagraph/docs/HISTORY_REMEDIATION.md`](regulagraph/docs/HISTORY_REMEDIATION.md).
- **Affiliation:** RegulaGraph is not affiliated with or endorsed by the BCT.
- **Licences:** dependency licences are recorded in [`regulagraph/LICENSING.md`](regulagraph/LICENSING.md). The project's own licence has not been chosen yet.
