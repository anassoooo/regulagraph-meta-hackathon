"""PDF text extraction with page-level provenance.

Page text is stored exactly as PyMuPDF returns it (no normalisation), so
``Evidence`` character offsets always index into the stored text.
Scanned pages without a text layer yield empty text; OCR is out of scope.
"""

import logging
from pathlib import Path

import pymupdf

from app.core import ids
from app.core.errors import PDFExtractionError
from app.models.schemas import Document, ExtractedDocument, ExtractedPage

logger = logging.getLogger(__name__)


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
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise PDFExtractionError("file is not a readable PDF", details={"filename": filename}) from exc

    with pdf:
        if pdf.needs_pass:
            raise PDFExtractionError("PDF is password-protected", details={"filename": filename})
        if pdf.page_count == 0:
            raise PDFExtractionError("PDF has no pages", details={"filename": filename})

        content_hash = ids.sha256_hex(data)
        doc_id = ids.document_id(content_hash)
        title = (pdf.metadata or {}).get("title") or None
        pages = [
            ExtractedPage(document_id=doc_id, page_number=index + 1, text=page.get_text("text"))
            for index, page in enumerate(pdf)
        ]

    document = Document(sha256=content_hash, filename=filename, title=title, page_count=len(pages))
    empty = [p.page_number for p in pages if not p.has_text]
    if empty:
        logger.warning("document %s: %d page(s) without a text layer: %s", doc_id, len(empty), empty)
    return ExtractedDocument(document=document, pages=pages)
