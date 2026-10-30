"""Recording integrity: a corrupted, inconsistent or tampered recording fails closed (HTTP 503), never 200 or 500.

Every probe mutates a temporary copy of eval/results/; the committed recordings are
hashed before and after each test to prove they are never altered.
"""

import hashlib
import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import replay as replay_service
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "eval" / "results"
SCENARIO_IDS = ["E01", "E12", "E05", "E13"]

needs_corpus = pytest.mark.skipif(
    not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"),
    reason="corpus PDFs not downloaded",
)


def _hashes() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(COMMITTED.glob("*.json"))}


@pytest.fixture
def results(tmp_path, monkeypatch):
    """A temporary copy of eval/results/ that the replay service reads instead of the committed one."""
    before = _hashes()
    copy = tmp_path / "results"
    copy.mkdir()
    for p in COMMITTED.glob("*.json"):
        shutil.copy(p, copy / p.name)
    monkeypatch.setattr(replay_service, "RESULTS_DIR", copy)
    monkeypatch.setattr(replay_service, "CURRENT_SUMMARY", copy / "current_scoring_summary.json")
    yield copy
    assert _hashes() == before, "committed recordings were modified"


@pytest.fixture
def client(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("replay must not make network calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    from app.services.meta_client import MetaModelAdapter

    monkeypatch.setattr(MetaModelAdapter, "complete", no_network)
    return TestClient(create_app())  # raise_server_exceptions=True: an uncaught error fails the test


def _summary(results: Path) -> dict[str, Any]:
    return json.loads((results / "current_scoring_summary.json").read_text(encoding="utf-8"))


def mutate_record(results: Path, case_id: str, fn: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
    """Apply fn(record, file_data) to the case's record in its latest recording file."""
    path = results / _summary(results)["latest_run_per_case"][case_id]["file"]
    data = json.loads(path.read_text(encoding="utf-8"))
    (record,) = [r for r in data["results"] if r["case_id"] == case_id]
    fn(record, data)
    path.write_text(json.dumps(data), encoding="utf-8")


def mutate_summary(results: Path, fn: Callable[[dict[str, Any]], None]) -> None:
    path = results / "current_scoring_summary.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def assert_fails_closed(response: httpx.Response, check: str | None = None) -> None:
    assert response.status_code == 503, response.text
    body = response.json()
    assert set(body) == {"error"}, "a failed replay must return no relationships and no evaluation"
    assert body["error"]["code"] == "demo_unavailable"
    assert "restore eval/results" in body["error"]["message"] or "fetch_corpus" in body["error"]["message"]
    if check is not None:
        assert check in body["error"]["details"]["check"], body["error"]


# --- Sol's E12 mutation ---------------------------------------------------------------------------


@needs_corpus
def test_e12_implements_changed_to_repeals_fails_closed(results, client) -> None:
    """Sol's repro: the model output says 'repeals', the stored report and evaluation still say 'implements'."""
    assert client.post("/api/scenarios/E12/replay").json()["evaluation"]["validated"] is True  # intact baseline

    def to_repeals(record, _):
        assert '"implements"' in record["model_call"]["raw_text"]
        record["model_call"]["raw_text"] = record["model_call"]["raw_text"].replace('"implements"', '"repeals"')

    mutate_record(results, "E12", to_repeals)
    r = client.post("/api/scenarios/E12/replay")
    assert_fails_closed(r, "report fingerprint")
    assert "validated" not in r.text and "repeals" not in r.text


@needs_corpus
def test_e12_mutation_with_a_matching_forged_fingerprint_still_fails(results, client, monkeypatch) -> None:
    """Even if the stored digest and report ID are forged to match the mutated output, the stored evaluation
    no longer agrees with the current scorer run on the replay, so nothing is shown."""
    from app.core.config import get_settings

    def to_repeals(record, _):
        record["model_call"]["raw_text"] = record["model_call"]["raw_text"].replace('"implements"', '"repeals"')

    mutate_record(results, "E12", to_repeals)
    with pytest.raises(replay_service.DemoUnavailable):
        replay_service.replay("E12", get_settings())
    # Forge the fingerprint from what the pipeline now produces, leaving the stored evaluation unchanged.
    captured = {}

    def capture(sc, rec, outcome):
        captured["digest"] = outcome.finalized.content_digest
        captured["report_id"] = outcome.finalized.report().report_id

    with monkeypatch.context() as m:
        m.setattr(replay_service, "_verify_reproduction", capture)
        with pytest.raises(replay_service.DemoUnavailable, match="stored score does not match"):
            replay_service.replay("E12", get_settings())

    def forge(record, _):
        record["content_digest"] = captured["digest"]
        record["report"]["report_id"] = captured["report_id"]

    mutate_record(results, "E12", forge)
    assert_fails_closed(client.post("/api/scenarios/E12/replay"))


# --- Field, identity and metadata mutations ------------------------------------------------------


def _set(path: list[Any], value: Any) -> Callable[[dict[str, Any], dict[str, Any]], None]:
    def fn(record, data):
        target = data if path[0] == "@file" else record
        keys = path[1:] if path[0] == "@file" else path
        for k in keys[:-1]:
            target = target[k]
        target[keys[-1]] = value

    return fn


def _delete(*keys: str) -> Callable[[dict[str, Any], dict[str, Any]], None]:
    def fn(record, _):
        target = record
        for k in keys[:-1]:
            target = target[k]
        del target[keys[-1]]

    return fn


def _reverse_prompt(record, _):
    record["rescored"]["retrieved_provision_ids"].reverse()
    record["score"]["retrieved_provision_ids"].reverse()


def _drop_prompt_provision(record, _):
    for key in ("rescored", "score"):
        record[key]["retrieved_provision_ids"].pop()


RECORD_MUTATIONS = {
    # identity
    "case id": (_set(["case_id"], "E99"), "exactly one recorded call"),
    "question": (_set(["question"], "Quelle circulaire ?"), "question"),
    "report question": (_set(["report", "question"], "Quelle circulaire ?"), "report question"),
    "model id": (_set(["model_call", "model_id"], "another-model"), "model identity"),
    "run model": (_set(["@file", "summary", "model"], "another-model"), "model identity"),
    "inference model": (lambda r, _: r["report"]["inferences"][0].update(model_id="another-model"),
                        "report inference model identity"),
    # stored report metadata / fingerprint
    "fingerprint": (_set(["content_digest"], "0" * 64), "report fingerprint"),
    "fingerprint format": (_set(["content_digest"], "not-a-digest"), "content_digest format"),
    "report id": (_set(["report", "report_id"], "rep_00000000000000000000000000000000"), "report id"),
    "generated_at": (_set(["report", "generated_at"], "yesterday"), "generated_at malformed"),
    # prompt / provision metadata
    "prompt size": (lambda r, _: r.update(prompt_chars=r["prompt_chars"] + 1), "prompt size"),
    "prompt provisions disagree": (lambda r, _: r["rescored"]["retrieved_provision_ids"].reverse(),
                                   "differ between historical and current"),
    "prompt provisions reordered": (_reverse_prompt, None),
    "prompt provision dropped": (_drop_prompt_provision, None),
    "prompt provision unknown": (lambda r, _: [r[k]["retrieved_provision_ids"].__setitem__(0, "prov_unknown")
                                               for k in ("rescored", "score")], None),
    # stored evaluation
    "stored verdict": (lambda r, _: r["rescored"]["verdicts"][0].update(verdict="type_error"),
                       "stored score does not match"),
    "stored validated": (_set(["rescored", "validated"], False), "stored score does not match"),
    "stored scoring version": (_set(["rescored", "scoring_version"], 1), "scoring version"),
    # P2: missing / malformed / unusable
    "model_call missing": (_delete("model_call"), "model_call missing"),
    "model_call not an object": (_set(["model_call"], "oops"), "model_call has the wrong type"),
    "raw output missing": (_delete("model_call", "raw_text"), "raw_text missing"),
    "raw output null": (_set(["model_call", "raw_text"], None), "raw_text missing or empty"),
    "raw output empty": (_set(["model_call", "raw_text"], "  "), "raw_text missing or empty"),
    "raw output malformed JSON": (_set(["model_call", "raw_text"], '{"relationships": [{'), "not valid JSON"),
    "raw output unusable": (_set(["model_call", "raw_text"], '{"relationships": []}'), "replay does not reproduce"),
    "raw output wrong shape": (_set(["model_call", "raw_text"], '["x"]'), None),
    "outcome truncated": (_set(["model_call", "outcome"], "truncated"), "not a usable answer"),
    "usage malformed": (_set(["model_call", "usage"], {"prompt_tokens": "many"}), "usage malformed"),
    "usage missing": (_delete("model_call", "usage"), "usage missing"),
    "http status wrong type": (_set(["model_call", "http_status"], "200"), "http_status has the wrong type"),
    "rescored missing": (_delete("rescored"), "rescored missing"),
    "report missing": (_delete("report"), "report missing"),
    "report provisions malformed": (_set(["report", "provisions"], [{"label": "x"}]), "provision_id missing"),
}
_NEED_REPLAY = {"fingerprint", "report id", "prompt size", "prompt provisions reordered", "prompt provision dropped",
                "prompt provision unknown", "stored verdict", "stored validated", "raw output unusable",
                "raw output wrong shape"}


@pytest.mark.parametrize("name", list(RECORD_MUTATIONS))
@pytest.mark.parametrize("case_id", SCENARIO_IDS)
def test_record_mutation_fails_closed(name, case_id, results, client) -> None:
    if name in _NEED_REPLAY and needs_corpus.args[0]:
        pytest.skip("corpus PDFs not downloaded")
    fn, check = RECORD_MUTATIONS[name]
    mutate_record(results, case_id, fn)
    assert_fails_closed(client.post(f"/api/scenarios/{case_id}/replay"), check)


SUMMARY_MUTATIONS = {
    "file name traversal": (lambda s: s["latest_run_per_case"]["E12"].update(file="../../.env"), "summary file name"),
    "file missing": (lambda s: s["latest_run_per_case"]["E12"].update(file="live_20990101T000000Z.json"), None),
    "call entry removed": (lambda s: s.update(calls=[c for c in s["calls"] if c["case_id"] != "E12"]),
                           "summary call entry"),
    "summary contradicts itself": (lambda s: s["latest_run_per_case"]["E12"].update(validated=False),
                                   "contradicts itself"),
    "scoring version": (lambda s: s.update(scoring_version=1), "summary scoring_version"),
    "calls not a list": (lambda s: s.update(calls={"x": 1}), "calls has the wrong type"),
}


@pytest.mark.parametrize("name", list(SUMMARY_MUTATIONS))
def test_summary_mutation_fails_closed(name, results, client) -> None:
    fn, check = SUMMARY_MUTATIONS[name]
    mutate_summary(results, fn)
    r = client.post("/api/scenarios/E12/replay")
    if check is None:
        assert r.status_code == 503 and set(r.json()) == {"error"}
    else:
        assert_fails_closed(r, check)


@needs_corpus
def test_stored_evaluation_flipped_in_both_summary_entries_fails_closed(results, client) -> None:
    """Consistent but stale labels (validated=false for an answer the scorer validates) are not shown."""

    def flip(s):
        s["latest_run_per_case"]["E12"]["validated"] = False
        next(c for c in s["calls"] if c["file"] == s["latest_run_per_case"]["E12"]["file"]
             and c["case_id"] == "E12")["validated"] = False

    mutate_summary(results, flip)
    assert_fails_closed(client.post("/api/scenarios/E12/replay"), "scoring summary does not match")


def test_unreadable_recording_files_fail_closed(results, client) -> None:
    file = _summary(results)["latest_run_per_case"]["E12"]["file"]
    (results / file).write_text("{not json", encoding="utf-8")
    assert_fails_closed(client.post("/api/scenarios/E12/replay"))
    (results / "current_scoring_summary.json").write_text("[1, 2]", encoding="utf-8")
    assert_fails_closed(client.post("/api/scenarios/E01/replay"))


def test_malformed_summary_makes_the_evaluation_view_503_not_500(results, client) -> None:
    mutate_summary(results, lambda s: s["calls"][0].pop("validated"))
    r = client.get("/api/evaluation")
    assert r.status_code == 503 and r.json()["error"]["code"] == "demo_unavailable"


# --- Evaluation bound to the verified replay ------------------------------------------------------


@needs_corpus
@pytest.mark.parametrize("case_id", SCENARIO_IDS)
def test_evaluation_is_bound_to_the_verified_replay(case_id, results, client) -> None:
    d = client.post(f"/api/scenarios/{case_id}/replay").json()
    ev = d["evaluation"]
    assert ev["case_id"] == case_id == d["scenario"]["id"]
    assert ev["content_digest"] == d["content_digest"] and ev["recording"] == d["recording"]["file"]
    assert ev["validated"] is True and ev["scoring_version"] == 2
    assert "not a legal or semantic verification" in ev["meaning"]
    assert len(ev["verdicts"]) == len(d["relationships"])


# --- Preflight -----------------------------------------------------------------------------------


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.delenv("META_INFERENCE_ENABLED", raising=False)
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@needs_corpus
def test_preflight_rejects_the_e12_mutation(demo, results) -> None:
    def to_repeals(record, _):
        record["model_call"]["raw_text"] = record["model_call"]["raw_text"].replace('"implements"', '"repeals"')

    mutate_record(results, "E12", to_repeals)
    demo.check_recordings()  # fields are well-formed; only the replay can tell
    with pytest.raises(demo.PreflightError, match=r"E12: replay failed.*report fingerprint.*restore eval/results"):
        demo.check_replays(demo.Settings())


@pytest.mark.parametrize("name", ["model_call missing", "raw output null", "raw output malformed JSON", "case id"])
def test_preflight_reports_malformed_recordings_without_crashing(name, demo, results) -> None:
    fn, check = RECORD_MUTATIONS[name]
    mutate_record(results, "E05", fn)
    with pytest.raises(demo.PreflightError, match=rf"E05: .*{check}.*restore eval/results"):
        demo.check_recordings()
