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

from pypdf import PasswordType, PdfReader
from pypdf.errors import PyPdfError

from app.core import ids
from app.core.errors import PDFExtractionError
from app.models.schemas import Document, ExtractedDocument, ExtractedPage

logger = logging.getLogger(__name__)

# pypdf raises its own errors for most malformed input, but damaged content
# streams can surface as plain Python errors from its parser.
_PARSE_ERRORS = (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError)


def extract_pdf(path: str | Path) -> ExtractedDocument:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PDFExtractionError("cannot read PDF file", details={"filename": path.name}) from exc
    return extract_pdf_bytes(data, filename=path.name)


def extract_pdf_bytes(data: bytes, *, filename: str) -> ExtractedDocument:
    if not data:
        raise PDFExtractionError("PDF is empty", details={"filename": filename})

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
        title = (reader.metadata.title if reader.metadata else None) or None
        for index, page in enumerate(reader.pages):
            text = page.extract_text(extraction_mode="plain")
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
    return ExtractedDocument(document=document, pages=pages)
