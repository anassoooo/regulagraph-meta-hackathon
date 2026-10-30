"""Start the local RegulaGraph demo after a full preflight (replay only; no model is ever called).

    python scripts/fetch_corpus.py      # once, in advance: downloads the BCT PDFs and checks SHA-256
    python scripts/demo.py              # preflight, then serve http://127.0.0.1:8000
    python scripts/demo.py --check      # preflight only

Preflight, in order; any failure stops the demo with a message saying what to do:

1. every investigation PDF is present in data/raw/ and matches its manifest SHA-256;
2. every demo scenario has a recorded live run listed in eval/results/current_scoring_summary.json;
3. every scenario replays offline through the real pipeline, and the primary scenario (E01)
   reproduces the recorded run's content digest with two candidate relationships;
4. the port is free.

The server is started with META_INFERENCE_ENABLED=false, so even a misconfigured
environment cannot make a paid call from the demo.
"""

import argparse
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["META_INFERENCE_ENABLED"] = "false"  # replay-only demo, regardless of .env

from app.core.config import Settings  # noqa: E402
from app.core.errors import RegulaGraphError  # noqa: E402
from app.core.redaction import install_log_redaction  # noqa: E402
from app.services import replay as replay_service  # noqa: E402
from app.services.corpus import DEFAULT_RAW_DIR, load_document, load_manifest  # noqa: E402

PRIMARY_EXPECTED_RELATIONS = ["repeals", "supersedes"]


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
    try:
        summary = replay_service.current_summary()
    except RegulaGraphError as exc:
        raise PreflightError(f"{exc.message} ({replay_service.CURRENT_SUMMARY})") from exc
    for sc in replay_service.SCENARIOS:
        latest = summary.get("latest_run_per_case", {}).get(sc.id)
        if latest is None or not (replay_service.RESULTS_DIR / latest["file"]).is_file():
            raise PreflightError(f"{sc.id}: no recorded live run found in {replay_service.RESULTS_DIR}")
        _ok(f"{sc.id}: recording {latest['file']}")


def check_replays(settings: Settings) -> None:
    for sc in replay_service.SCENARIOS:
        try:
            result = replay_service.replay(sc.id, settings)
        except RegulaGraphError as exc:
            raise PreflightError(f"{sc.id}: replay failed: {exc.message} {exc.details}") from exc
        report = result.outcome.finalized.report()
        types = sorted(r.relation_type.value for r in report.relationships)
        if any(r.status != "candidate" for r in report.relationships):
            raise PreflightError(f"{sc.id}: a non-candidate relationship was produced")
        if sc.primary:
            recorded = json.loads((replay_service.RESULTS_DIR / result.recording.file).read_text(encoding="utf-8"))
            digest = next(r["content_digest"] for r in recorded["results"] if r.get("case_id") == sc.id)
            if result.outcome.finalized.content_digest != digest or types != PRIMARY_EXPECTED_RELATIONS:
                raise PreflightError(f"{sc.id}: replay does not reproduce the recorded run (got {types})")
            _ok(f"{sc.id}: replay reproduces the recorded run exactly ({', '.join(types)})")
        else:
            _ok(f"{sc.id}: replay {result.outcome.model_call.outcome}, {len(types)} candidate relationship(s)")


def check_port(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            raise PreflightError(f"port {port} on {host} is already in use. Use --port to choose another.")
    _ok(f"port {host}:{port} is free")


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
    args = parser.parse_args()
    try:
        preflight(args.host, args.port)
    except PreflightError as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        print("Demo not started.", file=sys.stderr)
        return 1
    if args.check:
        print("Preflight passed.")
        return 0
    import uvicorn

    print(f"Preflight passed. Open http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
