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

Recording integrity (fail closed): before use, every field the replay relies on
is checked for presence and type, and the recording must agree with the
scenario (case ID, question, recorded model). The model call must be a
successful completion under the provider contract (HTTP 200, finish_reason
"stop", no refusal or error, coherent token usage), and its metadata must equal
every other recorded copy (scoring summary, run summary). After the replay, the finalized
report must reproduce the recording exactly (content digest, report ID, prompt
provisions and size, model call) and publish the scenario's expected relation
types. The evaluation returned with a replay is recomputed by the current
scorer from that replay, and the stored scores must agree with it; stored
labels are never trusted on their own. Any mismatch is a ``DemoUnavailable``
(HTTP 503) and nothing is shown.
"""

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import RegulaGraphError
from app.services.corpus import DEFAULT_RAW_DIR
from app.services.evaluation import SCORING_VERSION, CorpusView, EvalCase, load_cases, load_corpus_view, score_case
from app.services.investigator import InvestigationOutcome, investigate
from app.services.meta_client import CompletionResult, ReplayAdapter, TokenUsage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
CURRENT_SUMMARY = RESULTS_DIR / "current_scoring_summary.json"
RECORDED_MODEL_ID = "muse-spark-1.3"  # every curated recording was made with this model
RESTORE_HINT = "restore eval/results/ from git (git checkout -- eval/results)"
_RECORDING_FILE = re.compile(r"live_\d{8}T\d{6}Z\.json")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# Provider response contract (app/services/meta_client.py): the client only accepts HTTP 200, and
# finish_reason is one of these. Only "stop" is a complete answer: "length" is truncated, "content_filter"
# is filtered, and tool/function calls are never requested.
PROVIDER_FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter", "function_call"})
SUCCESS_FINISH_REASON = "stop"
SUCCESS_HTTP_STATUS = 200


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    primary: bool
    expected_relations: tuple[str, ...]  # sorted relation types a usable replay must publish


# Curated demo scenarios (PM decision D2/D6): E01 primary; E12, E05, E13 secondary. E10 is evaluation-only.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("E01", "Explicit repeal: circular 2026-10 and circular 2018-16", True, ("repeals", "supersedes")),
    Scenario("E12", "Implementation and direction: Article 47 and Article 8 of circular 2026-10", False,
             ("implements",)),
    Scenario("E05", "Qualified reference ('sans préjudice'): Article 15 and Article 14 of circular 2026-10", False,
             ("references",)),
    Scenario("E13", "Qualified exclusion ('ne sont pas applicables'): Article 35 and Article 17 of circular 2026-10",
             False, ("references",)),
)


class ScenarioNotFound(RegulaGraphError):
    code = "scenario_not_found"
    http_status = 404


class DemoUnavailable(RegulaGraphError):
    """Corpus or recordings missing, corrupted or incompatible: fail safely, show nothing unverified."""

    code = "demo_unavailable"
    http_status = 503


def _corrupt(file: str, case_id: str, check: str) -> DemoUnavailable:
    return DemoUnavailable(f"recording failed its integrity check ({check}); {RESTORE_HINT}",
                           details={"file": file, "scenario_id": case_id, "check": check})


@dataclass(frozen=True)
class Recording:
    file: str
    case_id: str
    question: str
    recorded_at: datetime
    model_call: dict[str, Any]
    shown_provision_ids: list[str]
    prompt_chars: int
    content_digest: str
    report_id: str
    stored_score: dict[str, Any]  # the current scorer's result stored with the recording ("rescored")
    summary_call: dict[str, Any]  # the matching entry of current_scoring_summary.json


@dataclass(frozen=True)
class ReplayResult:
    scenario: Scenario
    case: EvalCase
    recording: Recording
    outcome: InvestigationOutcome
    evaluation: dict[str, Any]  # computed from this replay by the current scorer


def scenario(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise ScenarioNotFound("unknown scenario", details={"scenario_id": scenario_id})


def current_summary() -> dict[str, Any]:
    try:
        data = json.loads(CURRENT_SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoUnavailable("current scoring summary is missing or unreadable") from exc
    if not isinstance(data, dict):
        raise DemoUnavailable(f"current scoring summary is malformed; {RESTORE_HINT}")
    return data


def _get(obj: Any, key: str, typ: type | tuple[type, ...], fail: Any, where: str, *, nullable: bool = False) -> Any:
    """``obj[key]``, required to be present and of type ``typ``; anything else is recording corruption."""
    if not isinstance(obj, dict) or key not in obj:
        raise fail(f"{where}.{key} missing")
    value = obj[key]
    if value is None and nullable:
        return None
    types = typ if isinstance(typ, tuple) else (typ,)
    if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
        raise fail(f"{where}.{key} has the wrong type")
    return value


def load_recording(case: EvalCase) -> Recording:
    """The latest recorded live run of a case, as listed in the current scoring summary, fully validated."""
    summary = current_summary()
    file: Any = None

    def fail(check: str) -> DemoUnavailable:
        return _corrupt(file if isinstance(file, str) else "?", case.id, check)

    latest_runs = _get(summary, "latest_run_per_case", dict, fail, "summary")
    if case.id not in latest_runs:
        raise DemoUnavailable(f"no recorded live run for this scenario; {RESTORE_HINT}",
                              details={"scenario_id": case.id})
    latest = _get(latest_runs, case.id, dict, fail, "summary latest_run_per_case")
    file = _get(latest, "file", str, fail, "summary latest")
    if not _RECORDING_FILE.fullmatch(file):
        raise fail("summary file name")
    if summary.get("scoring_version") != SCORING_VERSION:
        raise fail("summary scoring_version")
    calls = [c for c in _get(summary, "calls", list, fail, "summary")
             if isinstance(c, dict) and c.get("file") == file and c.get("case_id") == case.id]
    if len(calls) != 1:
        raise fail("summary call entry")
    summary_call = calls[0]
    for key in ("correct", "validated"):
        if _get(summary_call, key, bool, fail, "summary call") != _get(latest, key, bool, fail, "summary latest"):
            raise fail(f"summary {key} contradicts itself")

    path = RESULTS_DIR / file
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoUnavailable(f"recording is missing or unreadable; {RESTORE_HINT}", details={"file": file}) from exc
    if not isinstance(data, dict) or data.get("kind") != "live_eval":
        raise fail("file kind")
    records = [r for r in _get(data, "results", list, fail, "file") if isinstance(r, dict) and r.get("case_id") == case.id]
    if len(records) != 1 or records[0].get("api_called", True) is not True:  # absent in the first recording format
        raise fail("exactly one recorded call for this case")
    record = records[0]

    # Scenario identity.
    if _get(record, "question", str, fail, "record") != case.question:
        raise fail("question")
    report = _get(record, "report", dict, fail, "record")
    if _get(report, "question", str, fail, "report") != case.question:
        raise fail("report question")

    # Model call: a usable recorded answer from the recorded model.
    mc = _get(record, "model_call", dict, fail, "record")
    model_id = _get(mc, "model_id", str, fail, "model_call")
    run_model = _get(_get(data, "summary", dict, fail, "file"), "model", str, fail, "file summary")
    if model_id != RECORDED_MODEL_ID or run_model != model_id:
        raise fail("model identity")
    if _get(mc, "outcome", str, fail, "model_call") != "ok":
        raise fail("model_call outcome is not a usable answer")
    raw_text = _get(mc, "raw_text", str, fail, "model_call", nullable=True)
    if not raw_text or not raw_text.strip():
        raise fail("model_call.raw_text missing or empty")
    try:
        json.loads(raw_text)
    except ValueError as exc:
        raise fail("model_call.raw_text is not valid JSON") from exc
    _check_model_call(mc, record, data, summary_call, fail)
    for inference in _get(report, "inferences", list, fail, "report"):
        if _get(inference, "model_id", str, fail, "report inference") != model_id:
            raise fail("report inference model identity")

    # Stored report metadata and the prompt that was shown.
    digest = _get(record, "content_digest", str, fail, "record")
    if not _SHA256.fullmatch(digest):
        raise fail("content_digest format")
    report_id = _get(report, "report_id", str, fail, "report")
    try:
        recorded_at = datetime.fromisoformat(_get(report, "generated_at", str, fail, "report").replace("Z", "+00:00"))
    except ValueError as exc:
        raise fail("report.generated_at malformed") from exc
    prompt_chars = _get(record, "prompt_chars", int, fail, "record")
    stored = _get(record, "rescored", dict, fail, "record")
    if stored.get("case_id") != case.id or stored.get("scoring_version") != SCORING_VERSION:
        raise fail("stored score case or scoring version")
    shown = _get(stored, "retrieved_provision_ids", list, fail, "rescored")
    if not shown or not all(isinstance(x, str) for x in shown) or len(set(shown)) != len(shown):
        raise fail("prompt provision ids")
    historical = record.get("score")
    if isinstance(historical, dict) and historical.get("retrieved_provision_ids") not in (None, shown):
        raise fail("prompt provision ids differ between historical and current score")
    report_provisions = {_get(p, "provision_id", str, fail, "report provision")
                         for p in _get(report, "provisions", list, fail, "report")}
    if not set(shown) <= report_provisions:
        raise fail("prompt provisions missing from the stored report")

    return Recording(
        file=file, case_id=case.id, question=case.question, recorded_at=recorded_at, model_call=mc,
        shown_provision_ids=shown, prompt_chars=prompt_chars, content_digest=digest, report_id=report_id,
        stored_score=stored, summary_call=summary_call,
    )


def _check_model_call(mc: dict[str, Any], record: dict[str, Any], data: dict[str, Any],
                      summary_call: dict[str, Any], fail: Any) -> None:
    """The recorded call must be a successful completion under the provider contract, and every copy of its
    metadata (scoring summary, run summary) must agree with it exactly. No expected values are invented:
    the recorded model_call is compared against the other recorded copies."""
    if not _get(mc, "response_id", str, fail, "model_call").strip():
        raise fail("model_call.response_id empty")
    if _get(mc, "http_status", int, fail, "model_call") != SUCCESS_HTTP_STATUS:
        raise fail("model_call.http_status is not a successful provider response")
    finish_reason = _get(mc, "finish_reason", str, fail, "model_call")
    if finish_reason not in PROVIDER_FINISH_REASONS:
        raise fail("model_call.finish_reason is not a provider finish reason")
    if finish_reason != SUCCESS_FINISH_REASON:
        raise fail("model_call.finish_reason is not a complete answer")
    if _get(mc, "refusal", str, fail, "model_call", nullable=True) is not None:
        raise fail("model_call.refusal present")
    if _get(mc, "error", dict, fail, "model_call", nullable=True) is not None:
        raise fail("model_call.error present")
    latency = _get(mc, "latency_ms", (int, float), fail, "model_call")
    if not latency > 0 or latency == float("inf"):
        raise fail("model_call.latency_ms invalid")
    effort = _get(mc, "reasoning_effort", str, fail, "model_call")
    max_tokens = _get(mc, "max_completion_tokens", int, fail, "model_call")
    try:
        usage = TokenUsage.model_validate(_get(mc, "usage", dict, fail, "model_call"), strict=True)
    except ValueError as exc:
        raise fail("model_call.usage malformed") from exc
    if (min(usage.prompt_tokens, usage.completion_tokens, usage.reasoning_tokens) < 0
            or usage.total_tokens != usage.prompt_tokens + usage.completion_tokens
            or usage.reasoning_tokens > usage.completion_tokens
            or usage.completion_tokens > max_tokens):
        raise fail("model_call.usage inconsistent")

    # Scoring summary entry for this call: an exact copy of the call metadata.
    copies = {
        "model_outcome": mc["outcome"], "finish_reason": finish_reason, "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens, "reasoning_tokens": usage.reasoning_tokens,
        "latency_ms": latency,
    }
    for key, value in copies.items():
        if key not in summary_call or type(summary_call[key]) is bool or summary_call[key] != value:
            raise fail(f"summary {key} contradicts model_call")

    # Run summary of the recording file: settings always; token totals when the file holds this call only.
    run = _get(data, "summary", dict, fail, "file")
    if _get(run, "max_completion_tokens", int, fail, "file summary") != max_tokens:
        raise fail("file summary max_completion_tokens contradicts model_call")
    if _get(run, "reasoning_effort", str, fail, "file summary") != effort:
        raise fail("file summary reasoning_effort contradicts model_call")
    calls_in_file = [r for r in data["results"] if isinstance(r, dict) and r.get("api_called", True) is True]
    if calls_in_file == [record]:
        tokens = _get(run, "tokens", dict, fail, "file summary")
        expected = {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens, "reasoning_tokens": usage.reasoning_tokens}
        required = ("prompt_tokens", "completion_tokens", "total_tokens")  # reasoning_tokens: later format only
        if any(k not in tokens for k in required) or any(
                k in tokens and (type(tokens[k]) is not int or tokens[k] != v) for k, v in expected.items()):
            raise fail("file summary tokens contradict model_call")


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
    case = cases.get(sc.id)
    if case is None:
        raise DemoUnavailable("scenario has no evaluation case", details={"scenario_id": sc.id})
    rec = load_recording(case)
    view = corpus_view(settings)
    sources, provisions = view.scope(case)
    by_id = {p.provision_id: p for ps in provisions.values() for p in ps}
    missing = [pid for pid in rec.shown_provision_ids if pid not in by_id]
    if missing:
        # Provision IDs are content-derived; a mismatch means the PDFs or the extractor changed.
        raise DemoUnavailable("recording does not match the current corpus extraction",
                              details={"file": rec.file, "unknown_provisions": len(missing)})
    mc = rec.model_call  # every field below was validated by load_recording
    result = CompletionResult(
        response_id=mc["response_id"],
        model_id=mc["model_id"],
        text=mc["raw_text"],
        refusal=mc["refusal"],
        finish_reason=mc["finish_reason"],
        usage=TokenUsage.model_validate(mc["usage"]),
        latency_ms=mc["latency_ms"],
        http_status=mc["http_status"],
    )
    try:
        outcome = investigate(
            case.question, sources, provisions, ReplayAdapter([result]), settings,
            selected=[by_id[pid] for pid in rec.shown_provision_ids],
            reasoning_effort=mc["reasoning_effort"],
            clock=lambda: rec.recorded_at,
        )
    except (ValueError, RegulaGraphError) as exc:
        raise _corrupt(rec.file, sc.id, "replay through the pipeline failed") from exc
    _verify_reproduction(sc, rec, outcome)
    evaluation = _bound_evaluation(case, rec, outcome, view)
    return ReplayResult(scenario=sc, case=case, recording=rec, outcome=outcome, evaluation=evaluation)


def _verify_reproduction(sc: Scenario, rec: Recording, outcome: InvestigationOutcome) -> None:
    """The replay must reproduce the recorded finalized report exactly, with a usable answer."""
    report = outcome.finalized.report()
    checks = {
        "model outcome": outcome.model_call.outcome == "ok",
        "model identity": outcome.model_call.model_id == rec.model_call["model_id"],
        "question": outcome.question == rec.question == report.question,
        "prompt provisions": outcome.retrieved_provision_ids == rec.shown_provision_ids,
        "prompt size": outcome.prompt_chars == rec.prompt_chars,
        "report id": report.report_id == rec.report_id,
        "report fingerprint": outcome.finalized.content_digest == rec.content_digest,
        "expected relation types": tuple(sorted(r.relation_type.value for r in report.relationships))
        == sc.expected_relations,
        "candidate status": all(r.status == "candidate" for r in report.relationships),
    }
    for check, ok in checks.items():
        if not ok:
            raise _corrupt(rec.file, sc.id, f"replay does not reproduce the recording: {check}")


def _bound_evaluation(case: EvalCase, rec: Recording, outcome: InvestigationOutcome,
                      view: CorpusView) -> dict[str, Any]:
    """Score THIS replay with the current scorer; stored labels are accepted only if they agree with it."""
    fresh = score_case(case, outcome, view).model_dump(mode="json")
    if fresh != rec.stored_score:
        raise _corrupt(rec.file, case.id, "stored score does not match the replayed answer")
    call = rec.summary_call
    stored = (call.get("correct"), call.get("validated"), call.get("model_outcome"), call.get("errors"),
              call.get("verdicts"))
    if stored != (fresh["correct"], fresh["validated"], fresh["model_outcome"], fresh["errors"],
                  [v["verdict"] for v in fresh["verdicts"]]):
        raise _corrupt(rec.file, case.id, "scoring summary does not match the replayed answer")
    return {
        "case_id": case.id,
        "correct": fresh["correct"],
        "validated": fresh["validated"],
        "verdicts": [v["verdict"] for v in fresh["verdicts"]],
        "errors": fresh["errors"],
        "scoring_version": SCORING_VERSION,
        "content_digest": outcome.finalized.content_digest,  # the replay this evaluation was computed from
        "recording": rec.file,
    }
