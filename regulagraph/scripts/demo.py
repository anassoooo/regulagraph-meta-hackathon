"""Start the local RegulaGraph demo after a full preflight (replay only; no model is ever called).

    python scripts/fetch_corpus.py      # once, in advance: downloads the BCT PDFs and checks SHA-256
    python scripts/demo.py              # preflight, then serve http://127.0.0.1:8000
    python scripts/demo.py --check      # preflight only
    python scripts/demo.py --live --confirm-paid-calls [--budget-usd 4.50]   # local live mode (paid calls)

Preflight, in order; any failure stops the demo with a message saying what to do:

1. every investigation PDF is present in data/raw/ and matches its manifest SHA-256;
2. every demo scenario has a recorded live run listed in eval/results/current_scoring_summary.json,
   with every field the replay needs present, well-typed and consistent with the scenario
   (case ID, question, recorded model, prompt provisions, report metadata);
3. every scenario replays offline through the real pipeline and reproduces its recording exactly
   (content digest, report ID, prompt), publishes the scenario's expected candidate relation types,
   and the current scorer, run on the replay, agrees with the stored evaluation;
4. the port is free.

By default the server is started with META_INFERENCE_ENABLED=false and LIVE_MODE_ENABLED=false,
so even a misconfigured environment cannot make a paid call from the demo.

``--live --confirm-paid-calls`` (Sprint 3.5) additionally enables the local live investigation:
it refuses a non-loopback host, needs MODEL_API_KEY, checks the spending ledger, and caps the
cumulative budget at --budget-usd (default 4.50, never above 5.00). Replay is unchanged.
"""

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["META_INFERENCE_ENABLED"] = "false"  # replay-only demo, regardless of .env
os.environ["LIVE_MODE_ENABLED"] = "false"  # live mode only through --live --confirm-paid-calls

from app.core.config import Settings  # noqa: E402
from app.core.errors import RegulaGraphError  # noqa: E402
from app.core.redaction import install_log_redaction  # noqa: E402
from app.services import live as live_service  # noqa: E402
from app.services import replay as replay_service  # noqa: E402
from app.services.corpus import DEFAULT_RAW_DIR, load_document, load_manifest  # noqa: E402
from app.services.evaluation import load_cases  # noqa: E402


class PreflightError(Exception):
    pass


def _ok(message: str) -> None:
    print(f"  ok    {message}")


def check_corpus(settings: Settings) -> None:
    for entry in load_manifest().documents:
        if entry.role != "investigation":
            continue
        path = DEFAULT_RAW_DIR / entry.filename
        if not path.is_file():
            raise PreflightError(f"{entry.key}: {path} is missing. Run: python scripts/fetch_corpus.py")
        try:
            load_document(entry, settings)  # verifies SHA-256 before parsing
        except RegulaGraphError as exc:
            raise PreflightError(
                f"{entry.key}: {exc.message}. Delete {path.name} and run: python scripts/fetch_corpus.py"
            ) from exc
        _ok(f"{entry.key}: {entry.filename} sha256 {entry.sha256[:12]}… verified")


def check_recordings() -> None:
    cases = {c.id: c for c in load_cases().cases}
    for sc in replay_service.SCENARIOS:
        try:
            rec = replay_service.load_recording(cases[sc.id])
        except RegulaGraphError as exc:
            raise PreflightError(f"{sc.id}: {exc.message} {exc.details} ({replay_service.CURRENT_SUMMARY})") from exc
        _ok(f"{sc.id}: recording {rec.file} (fields and metadata consistent)")


def check_replays(settings: Settings) -> None:
    # replay() fails closed unless the replay reproduces the recording exactly, publishes the expected
    # candidate relation types, and the stored evaluation agrees with the current scorer run on it.
    for sc in replay_service.SCENARIOS:
        try:
            result = replay_service.replay(sc.id, settings)
        except RegulaGraphError as exc:
            raise PreflightError(f"{sc.id}: replay failed: {exc.message} {exc.details}") from exc
        types = ", ".join(sc.expected_relations)
        validated = "validated" if result.evaluation["validated"] else "not validated"
        _ok(f"{sc.id}: replay reproduces the recorded run exactly ({types}; evaluation re-scored: {validated})")


def check_port(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            raise PreflightError(f"port {port} on {host} is already in use. Use --port to choose another.")
    _ok(f"port {host}:{port} is free")


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def enable_live(host: str, budget_usd: float) -> Settings:
    """Switch this process to live mode, after checks that need no network call."""
    if host not in LOOPBACK_HOSTS:
        raise PreflightError(f"live mode binds to the loopback interface only, not {host}")
    if not 0 < budget_usd <= live_service.HARD_BUDGET_CEILING_USD:
        raise PreflightError(f"--budget-usd must be in (0, {live_service.HARD_BUDGET_CEILING_USD:.2f}]")
    os.environ["META_INFERENCE_ENABLED"] = "true"
    os.environ["LIVE_MODE_ENABLED"] = "true"
    os.environ["LIVE_BUDGET_USD"] = repr(budget_usd)
    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = Settings()
    reason = live_service.disabled_reason(settings)
    if reason is not None:
        raise PreflightError(f"live mode unavailable: {reason}")
    try:
        st = live_service.budget_state(settings)
    except RegulaGraphError as exc:
        raise PreflightError(f"{exc.message} ({live_service.live_dir(settings)})") from exc
    _ok(f"live mode: model {settings.meta_model_id}; budget {st.spent_usd:.4f} of {st.ceiling_usd:.2f} USD used "
        f"(estimated); {st.calls_used} of {st.max_calls} calls; ledger {live_service.live_dir(settings)}")
    return settings


def preflight(host: str, port: int) -> Settings:
    settings = Settings()
    install_log_redaction(settings)
    print("RegulaGraph demo preflight (replay only; no model is called)")
    check_corpus(settings)
    check_recordings()
    check_replays(settings)
    check_port(host, port)
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true", help="run the preflight and exit")
    parser.add_argument("--live", action="store_true", help="also enable local live investigation (paid calls)")
    parser.add_argument("--confirm-paid-calls", action="store_true", help="required with --live")
    parser.add_argument("--budget-usd", type=float, default=4.50, help="cumulative live budget (max 5.00)")
    args = parser.parse_args()
    if args.live and not args.confirm_paid_calls:
        print("  FAIL  --live makes paid Meta calls; pass --confirm-paid-calls to enable it.", file=sys.stderr)
        return 2
    try:
        preflight(args.host, args.port)
        if args.live:
            enable_live(args.host, args.budget_usd)
    except PreflightError as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        print("Demo not started.", file=sys.stderr)
        return 1
    if args.check:
        print("Preflight passed.")
        return 0
    import uvicorn

    mode = "LIVE MODE (paid calls enabled)" if args.live else "replay only"
    print(f"Preflight passed ({mode}). Open http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
