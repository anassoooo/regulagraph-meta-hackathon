"""Sprint 3.5 live mode: allowlist, request protections, spending controls, review records.

No test here makes a network call: the real HTTP transport and MetaModelAdapter are patched to
fail, and the model is a fake adapter that records what it was sent. The corpus PDFs are needed
for the end-to-end tests (skipped otherwise).
"""

import hashlib
import importlib.util
import itertools
import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.errors import ModelProviderError
from app.main import create_app
from app.services import live as live_service
from app.services import replay as replay_service
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest
from app.services.evaluation import load_cases
from app.services.meta_client import ChatMessage, CompletionResult, TokenUsage

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
BASE = "http://127.0.0.1:8000"
TEST_KEY = "live-test-key-0123456789"
LIVE_KEYS = ["bct-2026-10-fr", "bct-2018-16-fr", "bct-2025-01-fr"]
E01_QUESTION = "Quelle circulaire la circulaire n°2026-10 abroge-t-elle ou remplace-t-elle ?"


def _corpus_present() -> bool:
    return all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation")


needs_corpus = pytest.mark.skipif(not _corpus_present(), reason="corpus PDFs not downloaded")


def _results_hashes() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(RESULTS.glob("*"))}


@pytest.fixture(autouse=True)
def recordings_untouched():
    before = _results_hashes()
    yield
    assert _results_hashes() == before, "eval/results changed"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    calls: list[object] = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("tests must not make network calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    from app.services.meta_client import MetaModelAdapter

    monkeypatch.setattr(MetaModelAdapter, "complete", refuse)
    yield calls
    assert not calls


class FakeModel:
    """Stands in for MetaModelAdapter: records each prompt and returns a fixed answer."""

    def __init__(self, text: str = '{"relationships": []}', *, usage=(3000, 1000), error: Exception | None = None,
                 gate: threading.Event | None = None) -> None:
        self.text = text
        self.usage = usage
        self.error = error
        self.gate = gate
        self.prompts: list[list[ChatMessage]] = []

    def complete(self, messages, **kwargs) -> CompletionResult:
        self.prompts.append(list(messages))
        if self.gate is not None:
            self.gate.wait(10)
        if self.error is not None:
            raise self.error
        usage = None if self.usage is None else TokenUsage(
            prompt_tokens=self.usage[0], completion_tokens=self.usage[1], total_tokens=sum(self.usage))
        return CompletionResult(response_id="fake-response", model_id="muse-spark-1.3", text=self.text,
                                finish_reason="stop", usage=usage, latency_ms=12.5)


@pytest.fixture
def live_env(monkeypatch, tmp_path):
    """Live mode on, with a test key (overriding any local .env), a temporary ledger and a fake model."""
    env = {"LIVE_MODE_ENABLED": "true", "META_INFERENCE_ENABLED": "true", "MODEL_API_KEY": TEST_KEY,
           "LIVE_DATA_DIR": str(tmp_path / "live"), "LIVE_BUDGET_USD": "4.5", "LIVE_MAX_CALLS": "40"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    model = FakeModel()
    monkeypatch.setattr(live_service, "MetaModelAdapter", lambda settings: model)
    yield model
    get_settings.cache_clear()


@pytest.fixture
def default_env(monkeypatch):
    """The ordinary configuration: live mode off (a local .env may still hold a key)."""
    monkeypatch.delenv("LIVE_MODE_ENABLED", raising=False)
    monkeypatch.setenv("META_INFERENCE_ENABLED", "false")
    get_settings.cache_clear()
    constructed: list[object] = []
    monkeypatch.setattr(live_service, "MetaModelAdapter", lambda settings: constructed.append(1))
    yield constructed
    get_settings.cache_clear()


def make_client(**kwargs) -> TestClient:
    return TestClient(create_app(), base_url=BASE, client=("127.0.0.1", 50000), **kwargs)


def post_live(client, body, **headers):
    return client.post("/api/live/investigations", json=body, headers={"Origin": BASE, **headers})


def live_dir() -> Path:
    return live_service.live_dir(get_settings())


def ledger_records() -> list[dict]:
    path = live_dir() / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.fixture
def view():
    return replay_service.corpus_view(Settings())


def doc_ids(view, keys):
    return [view.doc_id(k) for k in keys]


def body(view, keys=("bct-2026-10-fr", "bct-2018-16-fr"), question=E01_QUESTION, confirm=True):
    return {"question": question, "document_ids": doc_ids(view, keys), "confirm_paid_call": confirm}


def e01_answer() -> str:
    case = {c.id: c for c in load_cases().cases}["E01"]
    return replay_service.load_recording(case).model_call["raw_text"]


# --- disabled by default ----------------------------------------------------------------

def test_live_mode_is_off_by_default_and_nothing_runs(default_env, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path / "live"))
    client = make_client()
    status = client.get("/api/live/status").json()
    assert status["enabled"] is False and "live mode is off" in status["reason"] and status["budget"] is None
    r = post_live(client, {"question": "x", "document_ids": [], "confirm_paid_call": True})
    assert r.status_code == 403 and r.json()["error"]["code"] == "live_mode_disabled"
    assert client.get("/api/live/documents").status_code == 403
    assert default_env == [] and not (tmp_path / "live").exists()  # no adapter, no ledger


@pytest.mark.parametrize("override, reason", [
    ({"META_INFERENCE_ENABLED": "false"}, "Meta inference is disabled"),
    ({"MODEL_API_KEY": ""}, "MODEL_API_KEY is not configured"),
    ({"LIVE_MODE_ENABLED": "false"}, "live mode is off"),
])
def test_each_switch_is_required(live_env, monkeypatch, override, reason) -> None:
    for k, v in override.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    client = make_client()
    assert client.get("/api/live/status").json()["reason"].startswith(reason)
    assert post_live(client, {"question": "x"}).status_code == 403
    assert live_env.prompts == [] and ledger_records() == []


def test_budget_setting_cannot_exceed_the_authorisation(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_BUDGET_USD", "5.01")
    with pytest.raises(ValueError):
        Settings()


# --- browser request protections -------------------------------------------------------------

@needs_corpus
@pytest.mark.parametrize("headers, status, code", [
    ({"Origin": None}, 403, "live_request_refused"),
    ({"Origin": "http://evil.example"}, 403, "live_request_refused"),
    ({"Origin": "http://localhost:8000"}, 403, "live_request_refused"),  # not this server's origin
    ({"Sec-Fetch-Site": "cross-site"}, 403, "live_request_refused"),
    ({"Sec-Fetch-Site": "same-site"}, 403, "live_request_refused"),
])
def test_cross_origin_posts_are_refused(live_env, view, headers, status, code) -> None:
    client = make_client()
    h = {"Origin": BASE, **headers}
    r = client.post("/api/live/investigations", json=body(view),
                    headers={k: v for k, v in h.items() if v is not None})
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert live_env.prompts == [] and ledger_records() == []


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_simple_cross_site_content_types_are_refused(live_env, content_type) -> None:
    r = make_client().post("/api/live/investigations", content=b'{"confirm_paid_call": true}',
                           headers={"Origin": BASE, "Content-Type": content_type})
    assert r.status_code == 415 and live_env.prompts == []


def test_non_loopback_clients_and_foreign_host_headers_are_refused(live_env) -> None:
    remote = TestClient(create_app(), base_url=BASE, client=("192.168.1.20", 50000))
    assert remote.get("/api/live/status").status_code == 403
    assert remote.post("/api/live/investigations", json={}, headers={"Origin": BASE}).status_code == 403
    rebound = TestClient(create_app(), base_url="http://evil.example:8000", client=("127.0.0.1", 50000))
    r = rebound.post("/api/live/investigations", json={}, headers={"Origin": "http://evil.example:8000"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "live_request_refused"
    assert live_env.prompts == []


# --- request validation, before any call -------------------------------------------------------

@needs_corpus
@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_paid_call_needs_explicit_confirmation(live_env, view, confirm) -> None:
    b = body(view)
    if confirm is None:
        del b["confirm_paid_call"]
    else:
        b["confirm_paid_call"] = confirm
    r = post_live(make_client(), b)
    assert r.status_code == 422 and "Confirm the paid Meta call" in r.json()["error"]["message"]
    assert live_env.prompts == [] and ledger_records() == []


@needs_corpus
@pytest.mark.parametrize("case", ["empty", "not a list", "arabic", "unknown", "duplicate", "not strings"])
def test_only_the_three_french_documents_can_be_approved(live_env, view, case) -> None:
    arabic = next(e.document_id for e in load_manifest().documents if e.role == "quality_only")
    fr = view.doc_id("bct-2026-10-fr")
    ids = {"empty": [], "not a list": fr, "arabic": [fr, arabic], "unknown": ["doc_" + "0" * 32],
           "duplicate": [fr, fr], "not strings": [1]}[case]
    r = post_live(make_client(), {"question": "Q ?", "document_ids": ids, "confirm_paid_call": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_live_request"
    assert live_env.prompts == [] and ledger_records() == []


@needs_corpus
@pytest.mark.parametrize("question", ["", "   ", "x" * 501, "line one\nline two", None, 7])
def test_question_is_validated(live_env, view, question) -> None:
    r = post_live(make_client(), body(view, question=question))
    assert r.status_code == 422 and live_env.prompts == []


def test_malformed_json_body_is_a_422(live_env) -> None:
    r = make_client().post("/api/live/investigations", content=b"{not json",
                           headers={"Origin": BASE, "Content-Type": "application/json"})
    assert r.status_code == 422 and live_env.prompts == []


@needs_corpus
def test_documents_endpoint_lists_exactly_the_three_french_documents(live_env, view) -> None:
    docs = make_client().get("/api/live/documents").json()
    assert [d["document_id"] for d in docs] == doc_ids(view, LIVE_KEYS)
    assert all(set(d) == {"document_id", "reference", "title", "publication_date"} for d in docs)


# --- allowlist enforcement ------------------------------------------------------------------------

SUBSETS = [list(c) for n in (1, 2, 3) for c in itertools.combinations(LIVE_KEYS, n)]


def _context(prompt: list[ChatMessage], question: str) -> str:
    user = next(m for m in prompt if m.role == "user").content
    prefix = f"Question: {question}\n\nDocuments:\n"
    assert user.startswith(prefix)
    return user[len(prefix):]


@needs_corpus
@pytest.mark.parametrize("keys", SUBSETS, ids=["+".join(k[4:11] for k in s) for s in SUBSETS])
def test_retrieval_context_holds_only_approved_documents(live_env, view, keys) -> None:
    # The question names every circular, including excluded ones: that is the user's text, not source context.
    question = ("Quels liens entre la circulaire n°2026-10, la circulaire n°2018-16 et la circulaire n°2025-01 : "
                "abroge, modifie, remplace ou applique ?")
    r = post_live(make_client(), body(view, keys, question=question))
    assert r.status_code == 200, r.text
    assert len(live_env.prompts) == 1
    context = _context(live_env.prompts[0], question)
    excluded = [k for k in LIVE_KEYS if k not in keys]
    for k in excluded:
        assert view.doc_id(k) not in context
        for p in view.provisions[k]:
            assert p.provision_id not in context
    allowed_provs = {p.provision_id for k in keys for p in view.provisions[k]}
    import re
    shown = set(re.findall(r'<provision id="([^"]+)"', context))
    assert shown and shown <= allowed_provs
    assert set(re.findall(r'<document id="([^"]+)"', context)) == set(doc_ids(view, keys))
    out = r.json()
    assert out["approved_document_ids"] == doc_ids(view, keys)
    assert {d["document_id"] for d in out["documents"]} <= set(doc_ids(view, keys))
    run = live_service.load_run(get_settings(), out["report_id"])
    report = run.outcome.finalized.report()
    assert set(report.document_ids) <= set(doc_ids(view, keys))
    assert {p.document_id for p in report.provisions} <= set(doc_ids(view, keys))


@needs_corpus
def test_excluded_document_text_is_not_sent_even_when_the_question_quotes_it(live_env, view) -> None:
    # The question repeats a sentence from 2018-16; the sentence may appear in the question but the
    # source context must not hold any 2018-16 provision.
    p = next(p for p in view.provisions["bct-2018-16-fr"] if p.kind == "article" and len(p.text) > 80)
    snippet = " ".join(p.text.split())[:120]
    question = f"Que signifie : « {snippet} » ?"
    r = post_live(make_client(), body(view, ["bct-2026-10-fr"], question=question))
    assert r.status_code == 200, r.text
    context = _context(live_env.prompts[0], question)
    assert p.provision_id not in context and view.doc_id("bct-2018-16-fr") not in context


@needs_corpus
def test_answer_targeting_an_excluded_document_is_rejected(live_env, view) -> None:
    live_env.text = e01_answer()  # the recorded E01 answer: 2026-10 Article 52 repeals 2018-16
    both = post_live(make_client(), body(view))
    assert both.status_code == 200 and {r["relation_type"] for r in both.json()["relationships"]} == {
        "repeals", "supersedes"}
    only = post_live(make_client(), body(view, ["bct-2026-10-fr"]))
    assert only.status_code == 200, only.text
    out = only.json()
    assert out["relationships"] == [] and out["state"] == "empty"
    assert {x["reason"] for x in out["rejected"]} == {"unknown_target"}
    assert view.doc_id("bct-2018-16-fr") not in json.dumps(out)


@needs_corpus
def test_live_relationships_are_candidates_with_reverified_evidence(live_env, view) -> None:
    live_env.text = e01_answer()
    out = post_live(make_client(), body(view)).json()
    assert out["mode"] == "live" and out["call"]["model_id"] == "muse-spark-1.3"
    for rel in out["relationships"]:
        assert rel["status"] == "candidate" and rel["confirmed"] is False and rel["ai_generated"] is True
        assert rel["evidence"] and all(e["source_text_verified"] for e in rel["evidence"])
    assert "provision_text" not in json.dumps(out) and "text" not in out["provisions"][0]


def _scope(view, keys):
    return live_service.build_scope(keys, view)


@needs_corpus
def test_prompt_check_refuses_excluded_context_before_reserving_or_calling(live_env, view) -> None:
    settings = get_settings()
    scope = _scope(view, ["bct-2026-10-fr"])
    bad = view.provisions["bct-2018-16-fr"][3]
    q = "Q ?"
    for content in (
        f'Question: {q}\n\nDocuments:\n<document id="{view.doc_id("bct-2026-10-fr")}">t</document>\n\nProvisions:\n'
        f'<provision id="{bad.provision_id}" document="x" label="y">\n{bad.text}\n</provision>',
        f'Question: {q}\n\nDocuments:\n<document id="{view.doc_id("bct-2018-16-fr")}">t</document>',
        f'Question: {q}\n\nDocuments:\n<document id="{view.doc_id("bct-2026-10-fr")}">t</document> '
        f'{view.doc_id("bct-2025-01-fr")}',
        "Question: something else\n\nDocuments:\n",
    ):
        guarded = live_service.GuardedAdapter(live_env, q, scope, live_service.Ledger(settings))
        with pytest.raises(live_service.AllowlistViolation):
            guarded.complete([ChatMessage(role="system", content="s"), ChatMessage(role="user", content=content)])
    assert live_env.prompts == [] and ledger_records() == []


@needs_corpus
def test_outcome_check_refuses_a_report_outside_the_scope(live_env, view) -> None:
    live_env.text = e01_answer()
    out = post_live(make_client(), body(view)).json()
    run = live_service.load_run(get_settings(), out["report_id"])
    with pytest.raises(live_service.AllowlistViolation):
        live_service.check_outcome(run.outcome, _scope(view, ["bct-2026-10-fr"]))
    live_service.check_outcome(run.outcome, _scope(view, ["bct-2026-10-fr", "bct-2018-16-fr"]))


# --- spending controls ----------------------------------------------------------------------------------

@needs_corpus
def test_budget_is_reserved_before_and_settled_after_each_call(live_env, view) -> None:
    out = post_live(make_client(), body(view)).json()
    kinds = [r["kind"] for r in ledger_records()]
    assert kinds == ["reserve", "settle"]
    reserve, settle = ledger_records()
    assert reserve["amount_usd"] >= settle["amount_usd"] == out["call"]["estimated_cost_usd"]
    assert settle["amount_usd"] == round(3000 * 1.25e-6 + 1000 * 4.25e-6, 5)
    assert out["budget"]["spent_usd"] == settle["amount_usd"] and out["budget"]["calls_used"] == 1
    assert out["budget"]["ceiling_usd"] == 4.5


@needs_corpus
def test_call_refused_when_worst_case_exceeds_remaining_budget(live_env, view, monkeypatch) -> None:
    monkeypatch.setenv("LIVE_BUDGET_USD", "0.005")  # below any worst case at the default limits
    get_settings.cache_clear()
    r = post_live(make_client(), body(view))
    assert r.status_code == 429 and r.json()["error"]["code"] == "live_budget_exhausted"
    assert live_env.prompts == [] and ledger_records() == []


@needs_corpus
def test_call_cap_is_enforced(live_env, view, monkeypatch) -> None:
    monkeypatch.setenv("LIVE_MAX_CALLS", "1")
    get_settings.cache_clear()
    client = make_client()
    assert post_live(client, body(view)).status_code == 200
    r = post_live(client, body(view))
    assert r.status_code == 429 and len(live_env.prompts) == 1


@needs_corpus
def test_spending_is_cumulative_and_never_passes_the_ceiling(live_env, view, monkeypatch) -> None:
    monkeypatch.setenv("LIVE_BUDGET_USD", "0.2")
    monkeypatch.setenv("LIVE_MAX_CALLS", "200")
    get_settings.cache_clear()
    live_env.usage = None  # no usage reported: every call keeps its worst-case reservation
    client = make_client()
    statuses = [post_live(client, body(view)).status_code for _ in range(12)]
    assert 200 in statuses and statuses[-1] == 429
    st = live_service.budget_state(get_settings())
    assert st.spent_usd <= 0.2 and st.calls_used == statuses.count(200) == len(live_env.prompts)


@needs_corpus
def test_failed_call_keeps_the_worst_case_cost(live_env, view) -> None:
    live_env.error = ModelProviderError("upstream failed", status_code=500)
    out = post_live(make_client(), body(view)).json()
    assert out["state"] == "provider_error" and out["relationships"] == []
    reserve, settle = ledger_records()
    assert settle["amount_usd"] == reserve["amount_usd"] > 0


@needs_corpus
def test_unsettled_reservation_counts_as_spent(live_env, view) -> None:
    ledger = live_service.Ledger(get_settings())
    ledger.reserve(0.04)  # e.g. the process died during the call
    assert live_service.budget_state(get_settings()).spent_usd == 0.04


@needs_corpus
@pytest.mark.parametrize("tamper", ["edit amount", "delete line", "garbage"])
def test_tampered_ledger_blocks_live_calls(live_env, view, tamper) -> None:
    client = make_client()
    assert post_live(client, body(view)).status_code == 200
    path = live_dir() / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    if tamper == "edit amount":
        lines[1] = lines[1].replace('"amount_usd":', '"amount_usd":-1, "x":')
    elif tamper == "delete line":
        lines = lines[1:]
    else:
        lines.append("not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status = client.get("/api/live/status").json()
    assert status["enabled"] is False and "ledger.jsonl" in status["reason"]
    r = post_live(client, body(view))
    assert r.status_code == 503 and len(live_env.prompts) == 1


@needs_corpus
def test_concurrent_requests_make_one_call(live_env, view) -> None:
    live_env.gate = threading.Event()
    settings = get_settings()
    results: list[object] = []

    def run():
        try:
            results.append(live_service.run_live(settings, view, E01_QUESTION, doc_ids(view, LIVE_KEYS[:2]), True))
        except live_service.LiveBusy as exc:
            results.append(exc)

    first = threading.Thread(target=run)
    first.start()
    for _ in range(200):
        if live_env.prompts:
            break
        threading.Event().wait(0.01)
    second = threading.Thread(target=run)
    second.start()
    second.join(5)
    live_env.gate.set()
    first.join(10)
    assert len(live_env.prompts) == 1
    assert sum(isinstance(r, live_service.LiveBusy) for r in results) == 1
    assert [r["kind"] for r in ledger_records()] == ["reserve", "settle"]


# --- secrets -----------------------------------------------------------------------------------------------

@needs_corpus
def test_credentials_never_leave_in_responses_or_stored_runs(live_env, view) -> None:
    live_env.text = e01_answer().replace("abroge", f"abroge {TEST_KEY}", 1)
    r = post_live(make_client(), body(view))
    assert r.status_code == 200 and TEST_KEY not in r.text
    for path in live_dir().rglob("*"):
        if path.is_file():
            assert TEST_KEY not in path.read_text(encoding="utf-8")


# --- replay is unaffected ---------------------------------------------------------------------------------

@needs_corpus
def test_replay_still_makes_no_call_in_live_mode(live_env) -> None:
    client = make_client()
    for sid in ("E01", "E12", "E05", "E13"):
        assert client.post(f"/api/scenarios/{sid}/replay").status_code == 200
    assert live_env.prompts == [] and ledger_records() == []


# --- demo launcher ---------------------------------------------------------------------------------------

def _demo():
    spec = importlib.util.spec_from_file_location("demo_live_under_test", ROOT / "scripts" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_forces_live_mode_off_by_default(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_MODE_ENABLED", "true")
    monkeypatch.setenv("META_INFERENCE_ENABLED", "true")
    _demo()
    import os

    assert os.environ["LIVE_MODE_ENABLED"] == "false" and os.environ["META_INFERENCE_ENABLED"] == "false"
    get_settings.cache_clear()


def test_demo_live_needs_confirmation(monkeypatch, capsys) -> None:
    demo = _demo()
    monkeypatch.setattr("sys.argv", ["demo.py", "--live"])
    assert demo.main() == 2 and "--confirm-paid-calls" in capsys.readouterr().err


@pytest.mark.parametrize("host, budget", [("0.0.0.0", 4.5), ("192.168.1.20", 4.5), ("127.0.0.1", 5.5),
                                          ("127.0.0.1", 0)])
def test_demo_live_refuses_public_binding_and_over_budget(monkeypatch, host, budget) -> None:
    demo = _demo()
    with pytest.raises(demo.PreflightError):
        demo.enable_live(host, budget)
    get_settings.cache_clear()
