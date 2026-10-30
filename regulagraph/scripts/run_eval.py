"""Run the evaluation cases in eval/cases.json.

    python scripts/run_eval.py structural                      # free: validators only, no model
    python scripts/run_eval.py live --confirm-paid-call        # paid: one Muse call per case

``structural`` feeds hand-made proposals (gold answers and perturbations)
through the pipeline with a replayed response. It measures what validation
rejects and what it cannot detect. It is not a model benchmark.

``live`` needs META_INFERENCE_ENABLED=true and MODEL_API_KEY, plus the flag,
after you have checked your credit balance. Each case is one request capped at
META_MAX_COMPLETION_TOKENS output+reasoning tokens and META_MAX_PROMPT_CHARS of
provision text. Results, including model outcome, token usage and latency, are
written to eval/results/ exactly as observed.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.services.evaluation import (  # noqa: E402
    load_cases,
    load_corpus_view,
    retrieval_recall,
    score_case,
    structural_probes,
)
from app.services.investigator import investigate  # noqa: E402
from app.services.meta_client import MetaModelAdapter  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "eval" / "results"


def _write(kind: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{kind}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return out


def run_structural(settings: Settings) -> int:
    cases = load_cases()
    view = load_corpus_view(settings)
    results = [r for case in cases.cases for r in structural_probes(case, view, settings)]
    print(f"{'case':5} {'probe':38} {'expect':12} {'published':9} {'rejection':24} ok")
    for r in results:
        print(f"{r.case_id:5} {r.probe:38} {r.expectation:12} {str(r.published):9} {str(r.rejection):24} "
              f"{'yes' if r.as_expected else 'NO'}")
    by_exp = Counter((r.expectation, r.as_expected) for r in results)
    summary = {
        "probes": len(results),
        "accept_as_expected": by_exp[("accept", True)], "accept_total": sum(v for (e, _), v in by_exp.items() if e == "accept"),
        "reject_as_expected": by_exp[("reject", True)], "reject_total": sum(v for (e, _), v in by_exp.items() if e == "reject"),
        "undetectable_published": by_exp[("undetectable", True)],
        "undetectable_total": sum(v for (e, _), v in by_exp.items() if e == "undetectable"),
    }
    recall = [retrieval_recall(c, view, settings) for c in cases.cases if c.polarity == "positive"]
    print()
    print(f"{'case':5} {'source retrieved':17} {'target retrieved':17} {'provisions':10} prompt chars")
    for r in recall:
        print(f"{r.case_id:5} {str(r.source_retrieved):17} {str(r.target_retrieved):17} {r.retrieved:<10} {r.prompt_chars}")
    summary["retrieval_source_hits"] = sum(r.source_retrieved for r in recall)
    summary["retrieval_target_hits"] = sum(1 for r in recall if r.target_retrieved)
    summary["retrieval_target_total"] = sum(1 for r in recall if r.target_retrieved is not None)
    summary["retrieval_cases"] = len(recall)
    print(json.dumps(summary, indent=2))
    out = _write("structural", {
        "kind": "structural_probes",
        "note": "Hand-made proposals replayed through the pipeline, plus deterministic retrieval recall; "
                "no model was called.",
        "settings": {"max_prompt_chars": settings.meta_max_prompt_chars},
        "summary": summary,
        "retrieval": [r.model_dump() for r in recall],
        "results": [r.model_dump() for r in results],
    })
    print(f"written: {out}")
    return 0 if all(r.as_expected for r in results) else 1


def run_live(settings: Settings, confirmed: bool, only: list[str]) -> int:
    if not confirmed:
        print("refusing: pass --confirm-paid-call after checking your Meta credit balance", file=sys.stderr)
        return 2
    cases = load_cases()
    view = load_corpus_view(settings)
    adapter = MetaModelAdapter(settings)
    results, usage = [], Counter()
    for case in cases.cases:
        if only and case.id not in only:
            continue
        sources, provisions = view.scope(case)
        outcome = investigate(case.question, sources, provisions, adapter, settings)
        scored = score_case(case, outcome, view)
        results.append({"score": scored.model_dump(), "model_call": outcome.model_call.model_dump(),
                        "rejected": [r.model_dump() for r in outcome.rejected],
                        "content_digest": outcome.finalized.content_digest,
                        "report": json.loads(outcome.finalized.snapshot)})
        if outcome.model_call.usage:
            usage.update(outcome.model_call.usage.model_dump(exclude={"completion_tokens_details"}))
        print(f"{case.id} {case.phenomenon:36} outcome={outcome.model_call.outcome:14} "
              f"correct={scored.correct} errors={scored.errors}")
        if outcome.model_call.outcome in {"disabled", "provider_error"} and outcome.model_call.http_status in {None, 401, 402, 403}:
            print("stopping: inference is disabled or the provider refused the credentials/billing", file=sys.stderr)
            break
    summary = {
        "cases_run": len(results),
        "correct": sum(1 for r in results if r["score"]["correct"]),
        "tokens": dict(usage),
        "model": settings.meta_model_id,
    }
    print(json.dumps(summary, indent=2))
    out = _write("live", {"kind": "live_eval", "summary": summary, "results": results})
    print(f"written: {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["structural", "live"])
    parser.add_argument("--confirm-paid-call", action="store_true")
    parser.add_argument("--case", action="append", default=[], help="live mode: run only these case ids")
    args = parser.parse_args()
    settings = Settings()
    if args.mode == "structural":
        return run_structural(settings)
    return run_live(settings, args.confirm_paid_call, args.case)


if __name__ == "__main__":
    sys.exit(main())
