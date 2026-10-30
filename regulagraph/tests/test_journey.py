"""End-to-end demo journey: select E01 -> replay -> inspect two candidate relationships -> exact evidence.

The API side runs through FastAPI's TestClient; the UI side runs the browser's
own view logic (app/web/view.js) under Node. Also runs the JS unit tests.
Skipped when Node or the corpus PDFs are unavailable.
"""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_js_unit_tests_pass() -> None:
    result = subprocess.run([NODE, "--test", "tests/js/view.test.mjs", "tests/js/controller.test.mjs"], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


@needs_node
def test_e01_journey_from_scenario_to_exact_evidence(tmp_path, monkeypatch) -> None:
    if not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"):
        pytest.skip("corpus PDFs not downloaded")

    def no_network(*args, **kwargs):
        raise AssertionError("the demo journey must not make network calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    client = TestClient(create_app())

    page = client.get("/")
    assert page.status_code == 200 and 'src="app.js"' in page.text and "REPLAY MODE" in page.text
    scenarios = client.get("/api/scenarios").json()
    primary = next(s for s in scenarios if s["primary"])
    replay = client.post(f"/api/scenarios/{primary['id']}/replay").json()
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")
    (tmp_path / "replay.json").write_text(json.dumps(replay), encoding="utf-8")

    result = subprocess.run([NODE, "tests/js/journey.mjs", str(tmp_path / "scenarios.json"), str(tmp_path / "replay.json")],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    ui = json.loads(result.stdout)

    assert (ui["primary"], ui["mode"], ui["state"], ui["stateMessage"]) == ("E01", "replay", "ok", None)
    assert [n["kind"] for n in ui["nodes"]] == ["provision", "document"] or [n["kind"] for n in ui["nodes"]] == ["document", "provision"]
    assert sorted(i["edge"] for i in ui["inspected"]) == ["repeals", "supersedes"]
    for item in ui["inspected"]:
        assert item["badges"] == ["AI-generated", "Candidate", "Unconfirmed"]
        assert item["heading"].startswith("Circulaire n°2026-10, Article 52 ")
        assert item["heading"].endswith("Circulaire n°2018-16 (whole circular)")
        (ev,) = item["evidence"]
        assert ev["location"] == "Circulaire n°2026-10, page 33, characters 1158–1428"
        assert ev["verified"] is True
        assert ev["pdfUrl"] == "https://www.bct.gov.tn/bct/siteprod/documents/Cir_2026_10__fr.pdf#page=33"
        assert "abroge et remplace la circulaire n°2018-16" in ev["quote"]
        assert "not verified" in item["interpretationLabel"]
