from pathlib import Path

import pytest

from app.core.errors import EvidenceValidationError, PDFExtractionError
from app.services.document_parser import extract_pdf, extract_pdf_bytes
from app.services.evidence import evidence_from_span, locate_quote, verify_evidence

PAGES = ["Article 1\nLes banques doivent declarer.", "Article 2\nLa circulaire n 2018-06 est abrogee."]


def test_extracts_each_page_with_provenance(make_pdf) -> None:
    result = extract_pdf_bytes(make_pdf(PAGES, title="Circulaire test"), filename="c.pdf")

    assert result.document.page_count == 2
    assert result.document.title == "Circulaire test"
    assert [p.page_number for p in result.pages] == [1, 2]
    assert "Article 1" in result.pages[0].text
    assert "abrogee" in result.pages[1].text and "abrogee" not in result.pages[0].text
    assert all(p.document_id == result.document.document_id for p in result.pages)
    assert result.pages[1].page_id == f"{result.document.document_id}:p2"


def test_ids_are_deterministic_and_content_derived(make_pdf) -> None:
    data = make_pdf(PAGES)
    a = extract_pdf_bytes(data, filename="a.pdf")
    b = extract_pdf_bytes(data, filename="renamed.pdf")
    c = extract_pdf_bytes(make_pdf(["different"]), filename="a.pdf")

    assert a.document.document_id == b.document.document_id
    assert [p.page_id for p in a.pages] == [p.page_id for p in b.pages]
    assert a.document.document_id != c.document.document_id


def test_extract_from_path(make_pdf, tmp_path: Path) -> None:
    path = tmp_path / "circ.pdf"
    path.write_bytes(make_pdf(PAGES))
    assert extract_pdf(path).document.filename == "circ.pdf"


def test_blank_page_is_kept_without_text(make_pdf) -> None:
    result = extract_pdf_bytes(make_pdf(["Article 1", None]), filename="c.pdf")
    assert result.document.page_count == 2
    assert result.pages[0].has_text and not result.pages[1].has_text


@pytest.mark.parametrize(
    ("data", "message"),
    [(b"", "empty"), (b"not a pdf at all", "not a readable PDF")],
)
def test_invalid_input_raises_structured_error(data: bytes, message: str) -> None:
    with pytest.raises(PDFExtractionError) as exc:
        extract_pdf_bytes(data, filename="bad.pdf")
    assert message in exc.value.message
    assert exc.value.to_dict()["code"] == "pdf_extraction_failed"


def test_encrypted_pdf_is_rejected(make_pdf) -> None:
    with pytest.raises(PDFExtractionError, match="password"):
        extract_pdf_bytes(make_pdf(["secret"], password="pw"), filename="enc.pdf")


def test_missing_file_raises_structured_error(tmp_path: Path) -> None:
    with pytest.raises(PDFExtractionError, match="cannot read"):
        extract_pdf(tmp_path / "missing.pdf")


def test_located_quote_is_verifiable(make_pdf) -> None:
    doc = extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")
    ev = locate_quote(doc.pages[1], "est abrogee")

    assert ev.page_number == 2
    assert doc.pages[1].text[ev.char_start : ev.char_end] == "est abrogee"
    verify_evidence(ev, doc)
    assert locate_quote(doc.pages[1], "est abrogee").evidence_id == ev.evidence_id


def test_fabricated_quote_is_rejected(make_pdf) -> None:
    doc = extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")
    with pytest.raises(EvidenceValidationError, match="not found"):
        locate_quote(doc.pages[0], "est abrogee")  # text exists, but on page 2


def test_tampered_evidence_fails_verification(make_pdf) -> None:
    doc = extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")
    real = locate_quote(doc.pages[0], "Article 1")
    moved = real.model_copy(update={"page_number": 2})  # same offsets, wrong page
    with pytest.raises(EvidenceValidationError):
        verify_evidence(moved, doc)


def test_span_outside_page_is_rejected(make_pdf) -> None:
    doc = extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")
    with pytest.raises(EvidenceValidationError, match="outside"):
        evidence_from_span(doc.pages[0], 0, 10_000)


# Raw-text guarantees ---------------------------------------------------------
# These pin pypdf's exact "plain" output for simple content streams. If a pypdf
# upgrade changes extracted text, these fail and stored evidence offsets must be
# treated as invalid for documents extracted with the old version.

def test_page_text_is_exact_extractor_output(make_pdf) -> None:
    result = extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")
    assert [p.text for p in result.pages] == PAGES  # no trailing newline, nothing added


@pytest.mark.parametrize(
    "text",
    [
        "La circulaire est abrog\u00e9e.",  # French accents kept as-is
        "Article\u00a05 \u00ab\u00a0capital\u00a0\u00bb",  # non-breaking spaces not turned into spaces
        "a  b   c\tz",  # repeated spaces and tabs not collapsed
        "obliga-\ntion",  # line-end hyphenation not joined
        "  leading and trailing  ",  # no stripping
    ],
)
def test_text_is_not_normalised(make_pdf, text: str) -> None:
    page = extract_pdf_bytes(make_pdf([text]), filename="c.pdf").pages[0]
    assert page.text == text


def test_owner_password_only_pdf_is_readable(make_pdf) -> None:
    data = make_pdf(["Article 1"], owner_password="owner")
    assert extract_pdf_bytes(data, filename="restricted.pdf").pages[0].text == "Article 1"


def test_fixture_pdfs_are_byte_deterministic(make_pdf) -> None:
    assert make_pdf(PAGES) == make_pdf(PAGES)
