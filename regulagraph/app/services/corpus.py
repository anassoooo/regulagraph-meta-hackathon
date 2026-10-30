"""The document corpus: manifest of public BCT circulars and verified loading.

PDFs are not committed (see ``data/manifest.json`` "reuse"). They are fetched
into ``data/raw/`` by ``scripts/fetch_corpus.py``. Loading a document checks
its SHA-256 against the manifest before parsing, so the extraction is tied to
exactly the bytes the manifest describes.
"""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.core import ids
from app.core.config import Settings
from app.core.errors import PDFExtractionError
from app.models.schemas import Document, ExtractedDocument, Sha256Hex
from app.services.document_parser import extract_pdf_bytes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "manifest.json"
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"


class CorpusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9-]+$")
    reference: str
    title: str
    publication_date: date
    language: Literal["fr", "ar"]
    role: Literal["investigation", "quality_only"]
    source_url: HttpUrl
    filename: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.pdf$")
    sha256: Sha256Hex
    bytes: int = Field(gt=0)
    retrieved_on: date
    pages: int = Field(ge=1)
    extraction_notes: list[str] = Field(default_factory=list)

    @property
    def document_id(self) -> str:
        return ids.document_id(self.sha256)


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    reuse: str
    documents: list[CorpusEntry]

    def get(self, key: str) -> CorpusEntry:
        for entry in self.documents:
            if entry.key == key:
                return entry
        raise KeyError(key)

    def by_document_id(self) -> dict[str, CorpusEntry]:
        return {e.document_id: e for e in self.documents}


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Corpus:
    corpus = Corpus.model_validate(json.loads(path.read_text(encoding="utf-8")))
    keys = [e.key for e in corpus.documents]
    if len(set(keys)) != len(keys) or len({e.sha256 for e in corpus.documents}) != len(keys):
        raise ValueError("manifest keys and hashes must be unique")
    return corpus


def load_document(entry: CorpusEntry, settings: Settings, raw_dir: Path = DEFAULT_RAW_DIR) -> ExtractedDocument:
    path = raw_dir / entry.filename
    if not path.is_file():
        raise PDFExtractionError(
            "corpus PDF not downloaded; run scripts/fetch_corpus.py", details={"key": entry.key}
        )
    if path.stat().st_size > settings.max_pdf_bytes:
        raise PDFExtractionError(
            "PDF exceeds the size limit",
            details={"filename": entry.filename, "limit": "max_pdf_bytes", "max": settings.max_pdf_bytes},
        )
    data = path.read_bytes()
    actual = ids.sha256_hex(data)
    if actual != entry.sha256:
        raise PDFExtractionError(
            "corpus PDF does not match the manifest hash",
            details={"key": entry.key, "expected": entry.sha256, "actual": actual},
        )
    return attach_metadata(
        extract_pdf_bytes(
            data, filename=entry.filename, max_bytes=settings.max_pdf_bytes, max_pages=settings.max_pdf_pages
        ),
        entry,
    )


def attach_metadata(extracted: ExtractedDocument, entry: CorpusEntry) -> ExtractedDocument:
    """Return a re-validated copy carrying the manifest's reference, date and URL."""
    if extracted.document.sha256 != entry.sha256:
        raise PDFExtractionError("extraction is not of this corpus entry", details={"key": entry.key})
    document = Document.model_validate(
        {
            **extracted.document.model_dump(),
            "title": entry.title,
            "reference": entry.reference,
            "publication_date": entry.publication_date,
            "source_url": entry.source_url,
        }
    )
    return ExtractedDocument.model_validate({**extracted.model_dump(), "document": document.model_dump()})
