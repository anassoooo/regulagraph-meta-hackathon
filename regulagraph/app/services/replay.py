"""Deterministic replay of recorded Muse responses through the real investigation pipeline.

A replay is NOT live inference. It takes the raw model output recorded during
an authorised live run (``eval/results/live_*.json``) and feeds it, via
``ReplayAdapter``, through the same steps a live answer goes through: parsing,
ID resolution, verbatim quote location in the hash-verified corpus,
provision/relationship checks and ``finalize_report``. Evidence shown in a
replay is therefore re-verified against the source text every time.

To reproduce what the model actually saw, the prompt is restricted to the
provisions recorded for that run, and the report timestamp is the recorded
one, so the content digest is stable. No network request is made.
"""

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import RegulaGraphError
from app.services.corpus import DEFAULT_RAW_DIR
from app.services.evaluation import CorpusView, EvalCase, load_cases, load_corpus_view
from app.services.investigator import InvestigationOutcome, investigate
from app.services.meta_client import CompletionResult, ReplayAdapter, TokenUsage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
CURRENT_SUMMARY = RESULTS_DIR / "current_scoring_summary.json"


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    primary: bool


# Curated demo scenarios (PM decision D2/D6): E01 primary; E12, E05, E13 secondary. E10 is evaluation-only.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("E01", "Explicit repeal: circular 2026-10 and circular 2018-16", True),
    Scenario("E12", "Implementation and direction: Article 47 and Article 8 of circular 2026-10", False),
    Scenario("E05", "Qualified reference ('sans préjudice'): Article 15 and Article 14 of circular 2026-10", False),
    Scenario("E13", "Qualified exclusion ('ne sont pas applicables'): Article 35 and Article 17 of circular 2026-10",
             False),
)


class ScenarioNotFound(RegulaGraphError):
    code = "scenario_not_found"
    http_status = 404


class DemoUnavailable(RegulaGraphError):
    """Corpus or recordings missing, corrupted or incompatible: fail safely, show nothing unverified."""

    code = "demo_unavailable"
    http_status = 503


@dataclass(frozen=True)
class Recording:
    file: str
    case_id: str
    recorded_at: datetime
    model_call: dict[str, Any]
    shown_provision_ids: list[str]


@dataclass(frozen=True)
class ReplayResult:
    scenario: Scenario
    case: EvalCase
    recording: Recording
    outcome: InvestigationOutcome
    evaluation: dict[str, Any] | None


def scenario(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise ScenarioNotFound("unknown scenario", details={"scenario_id": scenario_id})


def current_summary() -> dict[str, Any]:
    try:
        return json.loads(CURRENT_SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoUnavailable("current scoring summary is missing or unreadable") from exc


def load_recording(case_id: str) -> Recording:
    """The latest recorded live run of a case, as listed in the current scoring summary."""
    latest = current_summary().get("latest_run_per_case", {}).get(case_id)
    if latest is None:
        raise DemoUnavailable("no recorded live run for this scenario", details={"scenario_id": case_id})
    path = RESULTS_DIR / latest["file"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoUnavailable("recording is missing or unreadable", details={"file": latest["file"]}) from exc
    records = [r for r in data.get("results", []) if r.get("case_id") == case_id and r.get("api_called", True)]
    if len(records) != 1:
        raise DemoUnavailable("recording does not contain exactly one call for this case", details={"file": path.name})
    record = records[0]
    shown = record["score"].get("retrieved_provision_ids") or [p["provision_id"] for p in record["report"]["provisions"]]
    return Recording(
        file=path.name,
        case_id=case_id,
        recorded_at=datetime.fromisoformat(record["report"]["generated_at"].replace("Z", "+00:00")),
        model_call=record["model_call"],
        shown_provision_ids=shown,
    )


_view_lock = threading.Lock()
_view: CorpusView | None = None


def corpus_view(settings: Settings) -> CorpusView:
    """Load, hash-check and segment the corpus once per process; fail safely if anything is wrong."""
    global _view
    with _view_lock:
        if _view is None:
            try:
                _view = load_corpus_view(settings)
            except RegulaGraphError as exc:
                raise DemoUnavailable(
                    "corpus unavailable: run scripts/fetch_corpus.py (hashes are verified)",
                    details={"cause": exc.code, **{k: v for k, v in exc.details.items() if k in {"key", "filename"}},
                             "raw_dir": DEFAULT_RAW_DIR.name},
                ) from exc
        return _view


def replay(scenario_id: str, settings: Settings) -> ReplayResult:
    sc = scenario(scenario_id)
    cases = {c.id: c for c in load_cases().cases}
    case = cases[sc.id]
    rec = load_recording(sc.id)
    view = corpus_view(settings)
    sources, provisions = view.scope(case)
    by_id = {p.provision_id: p for ps in provisions.values() for p in ps}
    missing = [pid for pid in rec.shown_provision_ids if pid not in by_id]
    if missing:
        # Provision IDs are content-derived; a mismatch means the PDFs or the extractor changed.
        raise DemoUnavailable("recording does not match the current corpus extraction",
                              details={"file": rec.file, "unknown_provisions": len(missing)})
    mc = rec.model_call
    result = CompletionResult(
        response_id=mc.get("response_id") or "recorded",
        model_id=mc.get("model_id") or settings.meta_model_id,
        text=mc.get("raw_text"),
        refusal=mc.get("refusal"),
        finish_reason=mc.get("finish_reason") or "stop",
        usage=TokenUsage.model_validate(mc["usage"]) if mc.get("usage") else None,
        latency_ms=mc.get("latency_ms"),
        http_status=mc.get("http_status") or 200,
    )
    outcome = investigate(
        case.question, sources, provisions, ReplayAdapter([result]), settings,
        selected=[by_id[pid] for pid in rec.shown_provision_ids],
        reasoning_effort=mc.get("reasoning_effort") or "low",
        clock=lambda: rec.recorded_at,
    )
    evaluation = next((c for c in current_summary().get("calls", []) if c.get("file") == rec.file
                       and c.get("case_id") == sc.id), None)
    return ReplayResult(scenario=sc, case=case, recording=rec, outcome=outcome, evaluation=evaluation)
