"""Sprint 3 demo API: replay-only, evidence-first, no Meta calls, safe failure."""

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.errors import PDFExtractionError
from app.main import WEB_DIR, create_app
from app.services import replay as replay_service
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest

RESULTS = Path(__file__).resolve().parents[1] / "eval" / "results"


def _corpus_present() -> bool:
    return all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation")


needs_corpus = pytest.mark.skipif(not _corpus_present(), reason="corpus PDFs not downloaded")


@pytest.fixture
def client(monkeypatch):
    # Replay must never reach the network: any HTTP client use fails the test.
    def no_network(*args, **kwargs):
        raise AssertionError("replay must not make network calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)  # the real network transport
    from app.services.meta_client import MetaModelAdapter

    monkeypatch.setattr(MetaModelAdapter, "complete", no_network)
    return TestClient(create_app())


@pytest.fixture
def fresh_view(monkeypatch):
    monkeypatch.setattr(replay_service, "_view", None)


def test_scenarios_are_the_curated_four_without_e10(client) -> None:
    data = client.get("/api/scenarios").json()
    assert [s["id"] for s in data] == ["E01", "E12", "E05", "E13"]
    assert [s["primary"] for s in data] == [True, False, False, False]
    assert all(s["question"] for s in data)


def test_live_inference_exists_only_under_api_live_and_is_off_by_default(client, monkeypatch) -> None:
    # Sprint 3.5 (PM override of D1): live routes exist, under /api/live only, and refuse unless live mode is on.
    from app.core.config import get_settings

    monkeypatch.delenv("LIVE_MODE_ENABLED", raising=False)
    get_settings.cache_clear()
    paths = [getattr(r, "path", "") for r in create_app().routes]
    assert all(p.startswith("/api/live/") for p in paths if "live" in p and p != "/health/live")
    assert client.post("/api/scenarios/E01/live").status_code in {404, 405}
    local = TestClient(create_app(), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = local.post("/api/live/investigations", json={"confirm_paid_call": True},
                   headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "live_mode_disabled"
    get_settings.cache_clear()


def test_unknown_and_evaluation_only_scenarios_are_404(client) -> None:
    for sid in ("E10", "E99", "../etc"):
        r = client.post(f"/api/scenarios/{sid}/replay")
        assert r.status_code in {404, 405}
    r = client.post("/api/scenarios/E10/replay")
    assert r.json()["error"]["code"] == "scenario_not_found"


@needs_corpus
def test_e01_replay_shows_two_candidate_relationships_with_exact_evidence(client) -> None:
    r = client.post("/api/scenarios/E01/replay")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "replay" and "not live inference" in d["notice"]
    assert d["state"] == "ok"
    assert sorted(x["relation_type"] for x in d["relationships"]) == ["repeals", "supersedes"]
    for rel in d["relationships"]:
        assert (rel["status"], rel["confirmed"], rel["ai_generated"], rel["basis"]) == ("candidate", False, True, "inferred")
        assert rel["target_kind"] == "document"
        (ev,) = rel["evidence"]
        assert (ev["page_number"], ev["char_start"], ev["char_end"]) == (33, 1158, 1428)
        assert ev["quote"].startswith("La présente circulaire abroge et remplace la circulaire n°2018-16")
        assert ev["source_text_verified"] is True
        assert ev["pdf_url"] == "https://www.bct.gov.tn/bct/siteprod/documents/Cir_2026_10__fr.pdf#page=33"
        assert rel["interpretation"]["verified"] is False
    assert [p["label"] for p in d["provisions"]] == ["Article 52"]
    assert [x["reference"] for x in d["documents"]] == ["Circulaire n°2026-10", "Circulaire n°2018-16"]
    assert d["evaluation"]["validated"] is True and d["evaluation"]["scoring_version"] == 2


@needs_corpus
def test_e01_replay_is_deterministic_and_reproduces_the_recorded_run(client) -> None:
    recorded = json.loads((RESULTS / "live_20261030T151756Z.json").read_text(encoding="utf-8"))["results"][0]
    a = client.post("/api/scenarios/E01/replay").json()
    b = client.post("/api/scenarios/E01/replay").json()
    assert a == b
    assert a["content_digest"] == recorded["content_digest"]
    assert a["recording"]["file"] == "live_20261030T151756Z.json"


@needs_corpus
@pytest.mark.parametrize("sid, types", [("E12", ["implements"]), ("E05", ["references"]), ("E13", ["references"])])
def test_secondary_scenarios_replay(client, sid, types) -> None:
    d = client.post(f"/api/scenarios/{sid}/replay").json()
    assert d["state"] == "ok" and [r["relation_type"] for r in d["relationships"]] == types
    assert all(r["status"] == "candidate" and r["evidence"] for r in d["relationships"])


@needs_corpus
def test_replay_never_returns_full_provision_text(client) -> None:
    d = client.post("/api/scenarios/E01/replay").json()
    assert all("text" not in p and "segments" not in p for p in d["provisions"])
    body = json.dumps(d, ensure_ascii=False)
    assert "Fethi Zouhaier Nouri" not in body  # text of Article 52 outside the cited sentence


@needs_corpus
def test_replay_response_contains_no_configured_credential(monkeypatch, fresh_view) -> None:
    dummy = "dummy-secret-key-0123456789abcdef"
    monkeypatch.setenv("MODEL_API_KEY", dummy)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        for path in ("/api/scenarios", "/api/evaluation"):
            assert dummy not in c.get(path).text
        assert dummy not in c.post("/api/scenarios/E01/replay").text
    finally:
        get_settings.cache_clear()


def test_missing_or_corrupt_corpus_fails_safely(client, monkeypatch, fresh_view) -> None:
    def broken(settings, corpus=None):
        raise PDFExtractionError("corpus PDF does not match the manifest hash", details={"key": "bct-2026-10-fr"})

    monkeypatch.setattr(replay_service, "load_corpus_view", broken)
    r = client.post("/api/scenarios/E01/replay")
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "demo_unavailable" and "fetch_corpus" in err["message"]
    assert "relationships" not in r.json()


def test_missing_recording_fails_safely(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(replay_service, "CURRENT_SUMMARY", tmp_path / "missing.json")
    r = client.post("/api/scenarios/E01/replay")
    assert r.status_code == 503 and r.json()["error"]["code"] == "demo_unavailable"


def test_evaluation_reports_scorer_v2_and_e10_truncated(client) -> None:
    d = client.get("/api/evaluation").json()
    assert d["scoring_version"] == 2 and "not a legal verification" in d["meaning"]
    status = {c["id"]: c["status"] for c in d["cases"]}
    assert status["E10"] == "truncated"
    assert {k for k, v in status.items() if v == "validated"} == {"E01", "E05", "E12", "E13"}
    assert {k for k, v in status.items() if v == "not_run"} == {"E02", "E03", "E04", "E06", "E07", "E08", "E09", "E11"}
    e10 = next(c for c in d["cases"] if c["id"] == "E10")
    assert (e10["finish_reason"], e10["completion_tokens"], e10["reasoning_tokens"]) == ("length", 2048, 2046)
    s = d["structural"]
    assert (s["probes"], s["accept_total"], s["reject_total"], s["undetectable_published"]) == (46, 7, 24, 15)
    assert "cannot detect semantic errors" in s["undetectable_meaning"]


@pytest.mark.parametrize("path", ["/", "/app.js", "/view.js", "/style.css", "/api/scenarios", "/api/evaluation"])
def test_security_headers_everywhere(client, path) -> None:
    r = client.get(path)
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "default-src 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_frontend_has_no_inline_code_external_origins_or_hardcoded_evidence() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(\s[^>]*)?>(.*?)</script>", html, re.S)
    assert scripts and all('src="' in attrs and not body.strip() for attrs, body in scripts)  # no inline script
    assert "<style" not in html and not re.search(r"\son[a-z]+\s*=", html)  # no inline styles or handlers
    for f in ("app.js", "view.js", "style.css", "index.html"):
        text = (WEB_DIR / f).read_text(encoding="utf-8")
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "eval(" not in text
        urls = re.findall(r"https?://[^\s\"'`)]+", text)
        assert all(u.startswith(("https://www.bct.gov.tn/", "http://www.w3.org/2000/svg")) for u in urls), urls
        for evidence_value in ("1158", "1428", "abroge", "2018-16", "page=33", "prov_", "doc_"):
            assert evidence_value not in text, (f, evidence_value)


def test_frontend_contains_no_credentials() -> None:
    from app.core.config import Settings
    from app.core.redaction import configured_secrets

    secrets = configured_secrets(Settings())  # the developer's local key, if any; never printed
    for path in WEB_DIR.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not any(s in text for s in secrets)
            assert "MODEL_API_KEY" not in text and "Bearer" not in text
