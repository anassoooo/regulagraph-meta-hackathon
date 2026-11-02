"""Sprint 5 compliance: BCT source text is never committed beyond short exact quotations.

Policy: the repository may hold exact quotations of at most 600 characters (MAX_QUOTE_CHARS, the model output
contract) with their source, plus IDs, labels, offsets, hashes and URLs. It must not hold PDFs or full
provision or page text. Recorded live runs keep provision IDs and segments, never provision text; replay and
rescoring rebuild the text from the hash-verified PDFs.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.corpus import DEFAULT_RAW_DIR, load_manifest
from app.services.investigator import MAX_QUOTE_CHARS

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
RESULTS = ROOT / "eval" / "results"
# Longer strings are allowed only here: the recorded model output, which replay feeds back verbatim (its quotes
# are bounded by the output contract and checked below), and the manifest's own reuse statement (project prose,
# not source text). The run-of-corpus-text test below is the authoritative check for source text anywhere.
LONG_STRING_ALLOWED = {"results[].model_call.raw_text", "reuse"}
LONG_STRING_CHARS = 700
WINDOW = MAX_QUOTE_CHARS + 1  # any run of corpus text longer than the longest allowed quotation

sys.path.insert(0, str(ROOT / "scripts"))

corpus_present = pytest.mark.skipif(
    not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"),
    reason="corpus PDFs not downloaded",
)


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True).stdout
        return [REPO / p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):  # not a git checkout: scan the source tree instead
        return [p for p in ROOT.rglob("*") if p.is_file() and ".venv" not in p.parts and "raw" not in p.parts]


def strings(obj, path=""):  # noqa: ANN001, ANN201
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v, f"{path}[]")


def test_no_pdf_is_tracked() -> None:
    for path in tracked_files():
        if path.is_file():
            with path.open("rb") as fh:
                assert fh.read(5) != b"%PDF-", path


def test_recordings_hold_no_provision_text() -> None:
    import minimize_recordings

    files = sorted(RESULTS.glob("live_*.json"))
    assert files
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert not minimize_recordings.has_provision_text(data), path.name
        assert data["source_text_removed"]["field"] == "results[].report.provisions[].text"
        for record in data["results"]:  # what replay needs is still there
            for p in record["report"]["provisions"]:
                assert p["provision_id"].startswith("prov_") and p["segments"], path.name


def test_committed_json_has_no_long_strings_except_model_output() -> None:
    for path in [*sorted((ROOT / "eval").rglob("*.json")), *sorted((ROOT / "examples").rglob("*.json")),
                 ROOT / "data" / "manifest.json"]:
        for where, s in strings(json.loads(path.read_text(encoding="utf-8"))):
            if len(s) > LONG_STRING_CHARS:
                assert where in LONG_STRING_ALLOWED, (path.name, where, len(s))
    for path in sorted(RESULTS.glob("live_*.json")):
        for record in json.loads(path.read_text(encoding="utf-8"))["results"]:
            raw = record["model_call"].get("raw_text")
            if raw:
                for rel in json.loads(raw).get("relationships", []):
                    assert all(len(q["text"]) <= MAX_QUOTE_CHARS for q in rel["quotes"]), path.name


@corpus_present
def test_no_tracked_file_contains_a_run_of_corpus_text_longer_than_a_quotation() -> None:
    from app.core.config import get_settings
    from app.services.corpus import load_document

    settings = get_settings()
    windows: set[int] = set()
    for entry in load_manifest().documents:
        if entry.role != "investigation":
            continue
        for page in load_document(entry, settings).pages:
            t = page.text
            windows.update(hash(t[i:i + WINDOW]) for i in range(max(len(t) - WINDOW + 1, 0)))
    assert len(windows) > 50_000
    offenders = []
    for path in tracked_files():
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".py", ".mjs", ".js", ".html", ".txt", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        texts = [text]
        if path.suffix == ".json":  # also the decoded strings: JSON escapes newlines and quotes
            texts += [s for _, s in strings(json.loads(text))]
        for t in texts:
            if len(t) >= WINDOW and any(hash(t[i:i + WINDOW]) in windows for i in range(len(t) - WINDOW + 1)):
                offenders.append(path.relative_to(REPO).as_posix())
                break
    assert offenders == [], f"files with more than {MAX_QUOTE_CHARS} consecutive characters of BCT text: {offenders}"


@corpus_present
def test_rescoring_minimized_recordings_reproduces_the_stored_scores() -> None:
    import run_eval

    from app.core.config import get_settings
    from app.services.evaluation import load_cases, load_corpus_view, score_case

    view = load_corpus_view(get_settings())
    cases = {c.id: c for c in load_cases().cases}
    checked = 0
    for path in sorted(RESULTS.glob("live_*.json")):
        for record in json.loads(path.read_text(encoding="utf-8"))["results"]:
            if not record.get("api_called", True):
                continue
            case_id = record.get("case_id") or record["score"]["case_id"]
            current = score_case(cases[case_id], run_eval._StoredOutcome(record, view), view).model_dump()
            assert json.loads(json.dumps(current, default=str)) == record["rescored"], (path.name, case_id)
            checked += 1
    assert checked == 6


@corpus_present
def test_rehydration_fails_closed() -> None:
    import run_eval

    from app.core.config import get_settings
    from app.services.evaluation import load_corpus_view

    view = load_corpus_view(get_settings())
    record = json.loads(sorted(RESULTS.glob("live_*.json"))[0].read_text(encoding="utf-8"))["results"][0]
    report = record["report"]
    unknown = {**report, "provisions": [{**report["provisions"][0], "provision_id": "prov_" + "0" * 32}]}
    with pytest.raises(ValueError, match="not in the corpus"):
        run_eval.rehydrate_report(unknown, view)
    tampered = {**report, "provisions": [{**report["provisions"][0], "text": "not the official text"}]}
    with pytest.raises(ValueError, match="differs from the corpus"):
        run_eval.rehydrate_report(tampered, view)


def test_new_live_recordings_are_written_without_provision_text(tmp_path, monkeypatch) -> None:
    import run_eval

    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)
    payload = {"kind": "live_eval", "results": [{"report": {"provisions": [
        {"provision_id": "prov_x", "segments": [{"page_number": 1, "char_start": 0, "char_end": 4}], "text": "full"}]}}]}
    out = run_eval._write("live", payload)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert "text" not in written["results"][0]["report"]["provisions"][0]
    assert written["results"][0]["report"]["provisions"][0]["segments"]
    assert written["source_text_removed"]["field"] == "results[].report.provisions[].text"
