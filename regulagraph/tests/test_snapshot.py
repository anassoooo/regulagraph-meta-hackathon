"""Sprint 5: the static snapshot exports exactly the pinned API responses and nothing it must not.

The build runs in a subprocess (it disables inference and clears the key in its own environment).
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.corpus import DEFAULT_RAW_DIR, load_manifest
from tests.test_replay_golden import EVALUATION_SHA256, GOLDEN, SCENARIOS_SHA256

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"),
    reason="corpus PDFs not downloaded",
)


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("snapshot") / "site"
    r = subprocess.run([sys.executable, "-W", "ignore", "scripts/build_snapshot.py", "--allow-dirty", "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exported_responses_are_the_golden_bytes(snapshot) -> None:
    for sid, (digest, size) in GOLDEN.items():
        body = snapshot / "api" / "scenarios" / sid / "replay.json"
        assert (sha(body), body.stat().st_size) == (digest, size), sid
    assert sha(snapshot / "api" / "scenarios.json") == SCENARIOS_SHA256
    assert sha(snapshot / "api" / "evaluation.json") == EVALUATION_SHA256


def test_snapshot_contains_only_the_page_and_the_read_only_responses(snapshot) -> None:
    files = sorted(p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file())
    assert files == sorted([
        "index.html", "app.js", "view.js", "style.css", "favicon.svg", "snapshot-manifest.json", "api/scenarios.json",
        "api/evaluation.json", *(f"api/scenarios/{sid}/replay.json" for sid in GOLDEN)])
    assert not any(p.read_bytes().startswith(b"%PDF") for p in snapshot.rglob("*") if p.is_file())
    for name in ("app.js", "view.js", "style.css", "favicon.svg"):
        assert sha(snapshot / name) == sha(ROOT / "app" / "web" / name), name


def test_snapshot_page_is_labelled_and_switched_to_static_mode(snapshot) -> None:
    html = (snapshot / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((snapshot / "snapshot-manifest.json").read_text(encoding="utf-8"))
    assert f'<meta name="regulagraph-snapshot" content="static" data-commit="{manifest["commit"]}' in html
    assert 'class="banner snapshot"' in html and "STATIC SNAPSHOT" in html
    assert "not re-verified in your browser" in html and "No model is called" not in html
    assert "https://www.bct.gov.tn/" in html  # source attribution footer
    assert manifest["verification"].startswith("Every quotation was located") and "does not re-verify" in manifest["verification"]
    for rel, meta in manifest["files"].items():
        assert sha(snapshot / rel) == meta["sha256"], rel


def test_check_output_refuses_long_source_text_and_pdfs(tmp_path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    code = r"""
import json, sys
from pathlib import Path
import build_snapshot as b
from app.core.config import get_settings
out = Path(sys.argv[1])
for name in b.WEB_FILES:
    (out / name).write_text("x", encoding="utf-8")
def attempt(bodies, extra=None):
    for rel, body in bodies.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(body)
    if extra:
        (out / extra[0]).write_bytes(extra[1])
    try:
        b.check_output(out, bodies, get_settings())
        return "accepted"
    except b.SnapshotError as exc:
        return str(exc)
    finally:
        for rel in list(bodies) + ([extra[0]] if extra else []):
            (out / rel).unlink()
print(json.dumps([
    attempt({"api/evaluation.json": json.dumps({"q": "a" * 800}).encode()}),
    attempt({"api/evaluation.json": b"{}"}, ("doc.pdf", b"%PDF-1.7")),
    attempt({"api/evaluation.json": b"%PDF-1.7"}),
]))
"""
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", code, str(tmp_path)], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", timeout=300, env={**__import__("os").environ, "PYTHONPATH": f"{ROOT}{__import__('os').pathsep}{ROOT / 'scripts'}"})
    assert r.returncode == 0, r.stderr[-2000:]
    long_text, stray_pdf, pdf_body = json.loads(r.stdout.strip().splitlines()[-1])
    assert "longer than 700 characters" in long_text
    assert "unexpected files" in stray_pdf and "doc.pdf" in stray_pdf
    assert "a PDF must never be exported" in pdf_body
