"""Ingestion limits, page-quality signals and the corpus manifest."""

import json
from pathlib import Path

import pytest

from app.core import ids
from app.core.errors import PDFExtractionError
from app.services.corpus import DEFAULT_MANIFEST, Corpus, load_document, load_manifest
from app.services.document_parser import extract_pdf, extract_pdf_bytes
from app.services.quality import assess_extraction, assess_page


# Limits -------------------------------------------------------------------

def test_size_limit_is_enforced_before_parsing(make_pdf) -> None:
    data = make_pdf(["x"])
    with pytest.raises(PDFExtractionError, match="size limit") as err:
        extract_pdf_bytes(data, filename="c.pdf", max_bytes=len(data) - 1)
    assert err.value.details["limit"] == "max_pdf_bytes"
    extract_pdf_bytes(data, filename="c.pdf", max_bytes=len(data))


def test_size_limit_is_checked_from_file_metadata(make_pdf, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "big.pdf"
    path.write_bytes(make_pdf(["x"]))
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("file must not be read"))
    with pytest.raises(PDFExtractionError, match="size limit"):
        extract_pdf(path, max_bytes=10)


def test_page_limit_is_enforced_before_extraction(make_pdf) -> None:
    data = make_pdf(["a", "b", "c"])
    with pytest.raises(PDFExtractionError, match="page limit") as err:
        extract_pdf_bytes(data, filename="c.pdf", max_pages=2)
    assert err.value.details == {"filename": "c.pdf", "limit": "max_pdf_pages", "max": 2, "actual": 3}
    assert extract_pdf_bytes(data, filename="c.pdf", max_pages=3).document.page_count == 3


def test_limits_come_from_settings(settings_factory) -> None:
    s = settings_factory(max_pdf_bytes=1000, max_pdf_pages=5)
    assert (s.max_pdf_bytes, s.max_pdf_pages) == (1000, 5)
    with pytest.raises(ValueError):
        settings_factory(max_pdf_pages=0)


# Page quality -------------------------------------------------------------

def test_empty_and_low_text_pages_are_flagged(make_pdf) -> None:
    doc = extract_pdf_bytes(make_pdf(["Article 1 : " + "texte " * 20, None, "12"]), filename="c.pdf")
    assert assess_extraction(doc).flagged_pages == {2: "empty", 3: "low_text"}


@pytest.mark.parametrize(
    "text, status",
    [
        ("�" + "a" * 50, "suspect_characters"),  # unmapped glyph
        ("ﺍ" + "a" * 50, "suspect_characters"),  # Arabic presentation form
        ("وعلى المنشور " * 10, "ok"),
        (" " + "a" * 50, "ok"),  # private-use bullet: counted, not flagged
    ],
)
def test_suspect_character_signals(text, status) -> None:
    assert assess_page(1, text).status == status


def test_quality_cannot_detect_plausible_but_wrong_text() -> None:
    """The digit substitution seen in the Arabic 2018-16 PDF passes every automatic signal."""
    assert assess_page(1, "وعلى المنشور عدد 09 لسنة4003 المؤرخ في 48 نوفمبو 4003 " * 3).status == "ok"


# Manifest -----------------------------------------------------------------

def test_committed_manifest_is_valid_and_complete() -> None:
    corpus = load_manifest()
    keys = {e.key for e in corpus.documents}
    assert {"bct-2026-10-fr", "bct-2018-16-fr", "bct-2025-01-fr"} <= keys
    for entry in corpus.documents:
        assert str(entry.source_url).startswith("https://www.bct.gov.tn/")
        assert entry.document_id == ids.document_id(entry.sha256)
        assert entry.extraction_notes
    assert [e.role for e in corpus.documents if e.language == "ar"] == ["quality_only", "quality_only"]


def test_manifest_rejects_duplicates(tmp_path: Path) -> None:
    data = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    data["documents"].append(data["documents"][0])
    path = tmp_path / "m.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_manifest(path)


def _entry_for(data: bytes):
    raw = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    entry = {**raw["documents"][0], "sha256": ids.sha256_hex(data), "bytes": len(data), "filename": "t.pdf"}
    return Corpus.model_validate({**raw, "documents": [entry]}).documents[0]


def test_load_document_checks_hash_and_attaches_metadata(make_pdf, tmp_path: Path, settings_factory) -> None:
    data = make_pdf(["Article 1 : x"])
    entry = _entry_for(data)
    (tmp_path / "t.pdf").write_bytes(data)
    doc = load_document(entry, settings_factory(), raw_dir=tmp_path)
    assert doc.document.reference == entry.reference
    assert str(doc.document.source_url) == str(entry.source_url)
    assert doc.document.publication_date == entry.publication_date

    (tmp_path / "t.pdf").write_bytes(make_pdf(["Article 1 : y"]))  # replaced with other bytes
    with pytest.raises(PDFExtractionError, match="manifest hash"):
        load_document(entry, settings_factory(), raw_dir=tmp_path)


def test_load_document_reports_missing_download(tmp_path: Path, settings_factory) -> None:
    entry = load_manifest().documents[0]
    with pytest.raises(PDFExtractionError, match="fetch_corpus"):
        load_document(entry, settings_factory(), raw_dir=tmp_path)


def test_load_document_applies_size_limit(make_pdf, tmp_path: Path, settings_factory) -> None:
    data = make_pdf(["Article 1 : x"])
    entry = _entry_for(data)
    (tmp_path / "t.pdf").write_bytes(data)
    with pytest.raises(PDFExtractionError, match="size limit"):
        load_document(entry, settings_factory(max_pdf_bytes=10), raw_dir=tmp_path)
