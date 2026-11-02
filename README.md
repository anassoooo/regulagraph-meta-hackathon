# RegulaGraph

**RegulaGraph checks what an AI model says about Tunisian banking regulations against the official text.**
Meta Muse proposes how circulars of the Banque Centrale de Tunisie (BCT) relate to each other: which article repeals, replaces, amends, implements or only cites another. RegulaGraph publishes a proposal only if every quotation it relies on is found, character for character, in the cited provision of the official PDF. Everything published stays an unconfirmed candidate for a human reviewer.

Built for the Meta Global AI Developer Hackathon (2026-10-26 to 2026-11-04). The application lives in [`regulagraph/`](regulagraph/README.md), which has the full technical documentation.

**Demo video:** _link to be added_ (2 min 28 s; the real application with one genuine live Muse call, narrated by a synthetic voice).

## The problem

In September 2026 the BCT published circular 2026-10 on payment institutions. Its Article 52 reads: "La présente circulaire abroge et remplace la circulaire n°2018-16". Banks, payment institutions and their compliance teams must work out which provisions changed, which were replaced and which are only cited. Today that means reading French PDFs side by side.

A language model can answer such questions fluently, but it can also:

- paraphrase a quotation, or invent one;
- cite an article that does not exist;
- read a qualified reference ("sans préjudice de", "ne sont pas applicables") as a repeal.

In regulatory work, an answer that cannot be traced to the exact source text is not usable.

## What RegulaGraph does

1. **Source:** five public BCT PDFs, each checked against a recorded SHA-256 before use. The three French circulars are used for investigation; the Arabic versions are not (one is unusable after extraction).
2. **Retrieval:** deterministic BM25 with fixed bonuses selects at most 8 provisions (articles), up to 24,000 characters.
3. **Proposal:** a single Meta Muse call, constrained by a strict JSON schema, proposes typed relationships between those provisions. Each proposal must include its quotations.
4. **Validation by code:**
   - every quotation must be found verbatim inside the provision it cites;
   - IDs must be ones the model was shown;
   - the source provision must contain supporting text;
   - quotes from unrelated provisions are rejected.
   One failed check rejects the whole proposal.
5. **Publication:** what survives is published as an **AI-generated, unconfirmed candidate**, with the quotation, page, character offsets and a link to the official PDF. Nothing in the system can mark a relationship as confirmed.
6. **Review:** in local live mode a reviewer can accept, reject or ask for more evidence. Decisions are stored separately from the report and never change its status.

### Why this is not a basic RAG chatbot

It does use retrieval, but the output and the checks are different:

| | Typical RAG chatbot | RegulaGraph |
|---|---|---|
| Output | Free-text answer | Typed relationships between identified provisions (a graph), plus rejected proposals and their reasons |
| Citations | Links or passages the answer may or may not rely on | Every quotation re-located at exact page and character offsets in the cited provision; otherwise the proposal is rejected |
| Failure handling | The answer is shown anyway | Failed proposals are rejected and listed; a truncated model response is reported, not retried or patched |
| Status of the result | Presented as the answer | Always an unconfirmed candidate; human review is recorded separately |
| Evaluation | Usually none | 13 hand-written cases including 6 negative ones, plus probes showing what the validators cannot catch |

### What the checks prove, and what they do not

| | Checked by | What it means |
|---|---|---|
| **Source fidelity** | Code, whenever an answer is produced or replayed | The quotation exists at these offsets in the cited provision. It proves where the words are, not what they mean. |
| **Semantic correctness** | Nobody automatically | In a validation test, 15 of 15 hand-authored proposals with a wrong relation type, or a forbidden link backed by a real quotation, passed every structural check. |
| **Human review** | A reviewer (local live mode) | Accept, reject or "needs more evidence". A reviewer's opinion, never a legal confirmation. |

**What it is not:**
- not an autonomous agent: one model call per question, with no multi-step planning or tool use;
- not corpus-wide impact tracing: each investigation covers the provisions retrieved for one question;
- not production software: a local tool and a static demo, with no authentication and no multi-user deployment;
- not legal advice, and not a benchmark (see Evaluation).

## What was built during the hackathon

**Everything in this repository was built during the hackathon.** The Cerebral Valley participant guide lists the official kickoff as **2026-10-26, 12:00 PM ET** (16:00 UTC). Submissions are due 2026-11-04, 9:00 PM ET. Every commit in this repository postdates the kickoff, as does the repository itself (created 2026-10-26 21:43 UTC).

Times are git author dates, set by the committer's clock and corroborated by GitHub's repository and pull-request timestamps. Eligibility is for the organisers to determine; this section only states the facts.

| When (US Eastern) | Commits | Content |
|---|---|---|
| 2026-10-26 17:43–19:47 (5 h 43 min to 7 h 47 min after kickoff) | `c6b9cc5`, `76648f8`, `f9e0ccc`, `8ffe7b3`, `cb20c97`, `c3e2901` (PRs #1, #2) | **Scaffolding only:** a one-line README, empty package layout (14 lines, two commits by the GitHub Copilot coding agent) and `.gitkeep` directories. No functionality. |
| 2026-10-26 20:34–21:07 | `2b6bd13`, `b5ac759`, `ff4d963` (PR #3, opened 20:35 ET) | Sprint 1 foundation: data schemas, PDF text extraction, the Meta Model API client and integrity checks. The first functional code. |
| 2026-10-30 to 2026-11-02 | all later commits (PRs #3–#9 and this release branch) | Everything else, listed below. |

Built in that period:
- the hash-verified BCT corpus and article-level provision spans with exact offsets;
- BM25 retrieval, the single-call Muse investigation and the deterministic validators;
- the 13-case evaluation set, its scorer and the 6 recorded live evaluation runs (all Muse calls on 2026-10-30);
- the web client: relationship graph, evidence inspector, evaluation view;
- local live mode with spending guards, a hash-chained ledger and human review;
- the replay-only static snapshot, and the source-text remediation and its tests;
- the demo video (recorded 2026-11-01).

## Meta Muse integration

Muse does the one step the code cannot: reading French regulatory text and proposing which provision repeals, replaces, amends, implements or only cites which other one, with the quotations that justify it. Everything around that step is deterministic: retrieval before it, validation and publication after it. Without Muse there are no proposals, only retrieved text. The project has not compared Muse with other models.

- **Model:** `muse-spark-1.3` through the Meta Model API (`/v1/chat/completions`).
- **Structured output:** `response_format` with a strict JSON schema. The schema has no confidence field, so the model cannot express certainty that the system might mistake for proof.
- **Settings:** `reasoning_effort=low`; output capped at 2,048 tokens for the recorded runs, and at 4,096 in local live mode.
- **Usage so far:** 11 requests, about **$0.116** in total (estimated from published prices):
  - 7 for the recorded evaluation (about $0.065: a smoke test and 6 runs over 5 cases). One run (E10) used 2,046 of its 2,048 tokens on reasoning and returned no answer. It is reported as truncated, not retried.
  - 4 in local live mode ($0.051), including the call shown in the demo video.
- **Replays:** the demo shows recorded Muse answers. Locally, each replay runs the recorded output through the real pipeline again and re-locates every quotation in the PDF text. Replay is always labelled as replay, and live mode never falls back to it.

## Try it

| Mode | Where | Model calls | Evidence check |
|---|---|---|---|
| **Hosted static snapshot** | Link to be added after PM approval | None | Done once, when the snapshot was built. The page says so, with the build commit and date. |
| **Local replay** (default) | `python scripts/demo.py` | None | On every replay, against the local PDFs |
| **Local live mode** | `python scripts/demo.py --live --confirm-paid-calls` | One paid call per run, with budget guard | On every run |

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

### Inspect replay and evaluation

1. The page opens on recorded scenario E01, "Which circular does circular 2026-10 repeal or replace?". The graph shows two candidate edges, `repeals` and `supersedes`, from Article 52 to circular 2018-16.
2. Click an edge. The evidence panel shows the exact quotation, its page and character offsets, and a link to the official PDF, plus the model's interpretation labelled *not verified*.
3. Try E12, E05 and E13. E13 is a qualified reference that must stay a reference.
4. Open **Evaluation** to see:
   - what the validators reject;
   - the 15 semantically wrong proposals that they cannot catch;
   - the cases that were never run live.
5. From the command line, `python scripts/demo.py --check` re-runs every recorded answer through the pipeline and confirms it reproduces the recorded result.

The video's live call answered the same question differently from the recorded E01 run: `repeals` from Article 52, and `references` from the preamble of 2026-10, instead of `repeals` and `supersedes`. Both answers passed every check. Muse answers can vary between runs, which is one reason every result stays a candidate.

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
   Open http://127.0.0.1:8000. The live form appears first. Tick the source circulars, type a question, tick "I confirm one paid Meta call" and run. Each run is one Muse call capped at 4,096 output tokens. Spending is estimated from published prices and recorded in `var/live/ledger.jsonl` (git-ignored). This is a local guard, not an account spending cap.

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

## Known limitations

- **Structural, not semantic:** the validators prove a quotation exists where it is cited. They cannot tell whether "repeals" is the right reading of it (see the 15 of 15 above).
- **Small, unreviewed evaluation:** 13 cases written by the author, 5 run live, no domain-expert review, no accuracy figure.
- **Narrow corpus:** three French circulars. Arabic is not investigated.
- **One question, one call:** no multi-step reasoning, and retrieval can miss a relevant provision; the answer then covers only what was retrieved.
- **Output varies:** the same question can produce different, equally well-quoted candidates (see the video note above).
- **Extracted text, not the printed page:** quotations come from the PDF text layer (see below).
- **Local guards only:** live-mode spending limits and review storage are per machine and per process, not account-level controls.

## Source documents and licensing

- **BCT documents:** the PDFs are the BCT's and are **not redistributed**: they are downloaded from bct.gov.tn and checked by hash. The BCT site terms allow downloads for personal purposes, exact quotation with the site cited as source, and links that name the site and open in a new window. RegulaGraph shows only short quotations, with their page and a link to the official PDF ([manifest](regulagraph/data/manifest.json)).
- **Official texts:** the site terms protect the site's content "sous réserve des dispositions légales en vigueur, exemptant les textes réglementaires officiels du droit d'auteur", and Tunisian Law 94-36, article 1 as amended by Law 2009-33, excludes "les textes officiels d'ordre législatif, administratif ou judiciaire" from copyright protection. Whether BCT circulars fall under that exclusion has not been confirmed by the BCT or by counsel, and nothing here is legal advice. Sources: [`regulagraph/docs/HISTORY_REMEDIATION.md`](regulagraph/docs/HISTORY_REMEDIATION.md#rights-basis-sources-read-2026-11-02).
- **Quotations are exact as extracted, not as printed.** They come from the PDF's text layer, which can differ visually from the printed page: line breaks, extra spaces, and words split by a space (E12 shows "le s dossiers" where the page reads "les dossiers"). The app says so under every quotation, and the official PDF is the reference.
- **Correction:** from 2026-10-30 to 2026-11-01 the recorded evaluation runs also contained full extracted provision text: 29,060 distinct characters of circular 2026-10 (about 41% of its extracted text) and 2,862 of circular 2018-16 (about 10%). It is removed from the current files, and a test now prevents it from returning. It remains in git history: see [`regulagraph/docs/HISTORY_REMEDIATION.md`](regulagraph/docs/HISTORY_REMEDIATION.md).
- **Affiliation:** RegulaGraph is not affiliated with or endorsed by the BCT.
- **Licences:** dependency licences are recorded in [`regulagraph/LICENSING.md`](regulagraph/LICENSING.md). The project's own licence has not been chosen yet.
