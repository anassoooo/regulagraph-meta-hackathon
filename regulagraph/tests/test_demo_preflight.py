"""scripts/demo.py preflight: fails safely with actionable messages."""

import importlib.util
import socket
from pathlib import Path

import pytest

from app.core.errors import PDFExtractionError
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo(monkeypatch):
    # demo.py forces META_INFERENCE_ENABLED=false at import; monkeypatch restores the original afterwards.
    monkeypatch.delenv("META_INFERENCE_ENABLED", raising=False)
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_forces_inference_off(demo) -> None:
    import os

    assert os.environ["META_INFERENCE_ENABLED"] == "false"


def test_missing_pdf_stops_the_demo(demo, monkeypatch, tmp_path, settings_factory) -> None:
    monkeypatch.setattr(demo, "DEFAULT_RAW_DIR", tmp_path)
    with pytest.raises(demo.PreflightError, match="missing. Run: python scripts/fetch_corpus.py"):
        demo.check_corpus(settings_factory())


def test_corrupted_pdf_stops_the_demo(demo, monkeypatch, settings_factory) -> None:
    if not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"):
        pytest.skip("corpus PDFs not downloaded")

    def corrupted(entry, settings):
        raise PDFExtractionError("corpus PDF does not match the manifest hash", details={"key": entry.key})

    monkeypatch.setattr(demo, "load_document", corrupted)
    with pytest.raises(demo.PreflightError, match="does not match the manifest hash.*fetch_corpus"):
        demo.check_corpus(settings_factory())


def test_missing_recordings_stop_the_demo(demo, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(demo.replay_service, "CURRENT_SUMMARY", tmp_path / "missing.json")
    with pytest.raises(demo.PreflightError, match="summary is missing"):
        demo.check_recordings()


def test_busy_port_stops_the_demo(demo) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        with pytest.raises(demo.PreflightError, match="already in use"):
            demo.check_port("127.0.0.1", port)


def test_full_preflight_passes_on_the_real_corpus(demo, monkeypatch, capsys) -> None:
    if not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"):
        pytest.skip("corpus PDFs not downloaded")
    monkeypatch.setattr(demo.replay_service, "_view", None)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    demo.preflight("127.0.0.1", free_port)
    out = capsys.readouterr().out
    assert "E01: replay reproduces the recorded run exactly (repeals, supersedes)" in out
