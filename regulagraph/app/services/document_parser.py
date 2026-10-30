"""PDF text extraction with page-level provenance (pypdf, BSD-3-Clause).

Page text is stored exactly as ``pypdf.PageObject.extract_text()`` returns it
in its default "plain" mode. This module applies no normalisation of its own
(no whitespace, Unicode, ligature or hyphenation changes), so ``Evidence``
character offsets always index into the stored text.

Note that pypdf's plain mode is itself a heuristic reconstruction of text
from the content stream (it decides where line breaks and spaces go); the
stored text is "what pypdf extracted", not a canonical rendering of the page.
Scanned pages without a text layer yield empty text; OCR is out of scope.
"""

import io
import logging
from pathlib import Path

import pypdf
from pypdf import PasswordType, PdfReader
from pypdf.errors import PyPdfError

from app.core import ids
from app.core.errors import PDFExtractionError
from app.models.schemas import (
    Document,
    ExtractedDocument,
    ExtractedPage,
    ExtractionProvenance,
    page_text_digest,
)

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "pypdf"
EXTRACTION_MODE = "plain"

# pypdf raises its own errors for most malformed input, but damaged content
# streams can surface as plain Python errors from its parser.
_PARSE_ERRORS = (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError)


DEFAULT_MAX_BYTES = 20_000_000
DEFAULT_MAX_PAGES = 200


def extract_pdf(
    path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES, max_pages: int = DEFAULT_MAX_PAGES
) -> ExtractedDocument:
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > max_bytes:  # checked before reading the file into memory
            raise _too_large(path.name, size, max_bytes)
        data = path.read_bytes()
    except OSError as exc:
        raise PDFExtractionError("cannot read PDF file", details={"filename": path.name}) from exc
    return extract_pdf_bytes(data, filename=path.name, max_bytes=max_bytes, max_pages=max_pages)


def _too_large(filename: str, size: int, max_bytes: int) -> PDFExtractionError:
    return PDFExtractionError(
        "PDF exceeds the size limit",
        details={"filename": filename, "limit": "max_pdf_bytes", "max": max_bytes, "actual": size},
    )


def extract_pdf_bytes(
    data: bytes, *, filename: str, max_bytes: int = DEFAULT_MAX_BYTES, max_pages: int = DEFAULT_MAX_PAGES
) -> ExtractedDocument:
    if not data:
        raise PDFExtractionError("PDF is empty", details={"filename": filename})
    if len(data) > max_bytes:
        raise _too_large(filename, len(data), max_bytes)

    try:
        reader = PdfReader(io.BytesIO(data))
    except _PARSE_ERRORS as exc:
        raise PDFExtractionError("file is not a readable PDF", details={"filename": filename}) from exc

    # Owner-password-only PDFs open with the empty user password (as in most
    # PDF viewers); anything needing a real user password is rejected.
    if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        raise PDFExtractionError("PDF is password-protected", details={"filename": filename})

    content_hash = ids.sha256_hex(data)
    doc_id = ids.document_id(content_hash)
    pages: list[ExtractedPage] = []
    try:
        page_count = len(reader.pages)
        if page_count > max_pages:  # checked before any page text is extracted
            raise PDFExtractionError(
                "PDF exceeds the page limit",
                details={"filename": filename, "limit": "max_pdf_pages", "max": max_pages, "actual": page_count},
            )
        title = (reader.metadata.title if reader.metadata else None) or None
        for index, page in enumerate(reader.pages):
            text = page.extract_text(extraction_mode=EXTRACTION_MODE)
            pages.append(ExtractedPage(document_id=doc_id, page_number=index + 1, text=text))
    except _PARSE_ERRORS as exc:
        raise PDFExtractionError(
            "failed to extract text from PDF",
            details={"filename": filename, "page_number": len(pages) + 1},
        ) from exc

    if page_count == 0:
        raise PDFExtractionError("PDF has no pages", details={"filename": filename})

    document = Document(sha256=content_hash, filename=filename, title=title, page_count=page_count)
    empty = [p.page_number for p in pages if not p.has_text]
    if empty:
        logger.warning("document %s: %d page(s) without a text layer: %s", doc_id, len(empty), empty)
    provenance = ExtractionProvenance(
        extractor_name=EXTRACTOR_NAME,
        extractor_version=pypdf.__version__,
        extraction_mode=EXTRACTION_MODE,
        text_digest=page_text_digest(content_hash, pages),
    )
    return ExtractedDocument(document=document, provenance=provenance, pages=pages)
