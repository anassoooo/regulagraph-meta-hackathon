"""One minimal, paid, live request to the Meta Model API (Muse Spark 1.3).

Opt-in only. It sends nothing unless ALL of these hold:

* ``META_INFERENCE_ENABLED=true`` and ``MODEL_API_KEY`` are set (env or .env);
* the ``--confirm-paid-call`` flag is passed, after you have checked your
  credit balance in the Meta developer dashboard (the API has no balance or
  spending-limit endpoint, so this script cannot check it for you).

Cost bound: one request, a short prompt (~60 input tokens) and at most
``--max-tokens`` output+reasoning tokens (default 64). At the published price
($1.25 / $4.25 per 1M input / output tokens) that is below $0.001.

The record (model id, token usage, latency, status, finish reason, rate-limit
headers; never the key) is printed and written to ``eval/results/``.

    META_INFERENCE_ENABLED=true python scripts/smoke_test.py --confirm-paid-call
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.errors import RegulaGraphError  # noqa: E402
from app.services.meta_client import ChatMessage, MetaModelAdapter  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "eval" / "results"
SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "smoke_test",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"],
            "additionalProperties": False,
        },
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm-paid-call", action="store_true", help="required: you checked your credit balance")
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args()

    settings = Settings()
    if not args.confirm_paid_call:
        print("refusing: pass --confirm-paid-call after checking your Meta credit balance", file=sys.stderr)
        return 2
    if args.max_tokens <= 0 or args.max_tokens > 256:
        print("refusing: --max-tokens must be between 1 and 256 for a smoke test", file=sys.stderr)
        return 2

    adapter = MetaModelAdapter(settings)
    record: dict[str, object] = {
        "kind": "smoke_test",
        "started_at": datetime.now(UTC).isoformat(),
        "model_requested": settings.meta_model_id,
        "max_completion_tokens": args.max_tokens,
    }
    try:
        result = adapter.complete(
            [
                ChatMessage(role="system", content="Reply with JSON only."),
                ChatMessage(role="user", content='Return {"status": "ok"}.'),
            ],
            max_completion_tokens=args.max_tokens,
            temperature=0,
            response_format=SCHEMA,
            reasoning_effort="minimal",
        )
    except RegulaGraphError as exc:
        record.update(outcome="error", error=exc.to_dict())
    else:
        parsed: object = None
        try:
            parsed = json.loads(result.text) if result.text else None
        except ValueError:
            pass
        record.update(
            outcome="ok" if parsed == {"status": "ok"} else "unexpected_output",
            model_id=result.model_id,
            response_id=result.response_id,
            http_status=result.http_status,
            finish_reason=result.finish_reason,
            refusal=result.refusal,
            usage=result.usage.model_dump() if result.usage else None,
            latency_ms=result.latency_ms,
            rate_limit_remaining=result.rate_limit_remaining,
            text=(result.text or "")[:200],
        )
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"smoke_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"written: {out}")
    return 0 if record["outcome"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
