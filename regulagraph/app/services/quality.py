"""Deterministic per-page extraction-quality signals.

These are coarse indicators computed from the stored page text. They flag
pages that are empty, nearly empty or contain characters a correct extraction
should not produce. They cannot detect wrong-but-plausible text (for example
digits mapped to the wrong glyphs, or Arabic letters reordered), which is why
the corpus manifest also records manual review notes per document.
"""

import unicodedata
from typing import Literal

from pydantic import BaseModel

from app.models.schemas import ExtractedDocument

LOW_TEXT_CHARS = 40  # fewer letters than this on a page is flagged

PageStatus = Literal["ok", "empty", "low_text", "suspect_characters"]


class PageQuality(BaseModel):
    page_number: int
    chars: int
    letters: int
    latin_letters: int
    arabic_letters: int
    replacement_chars: int  # U+FFFD: the extractor could not map a glyph
    private_use_chars: int  # U+E000-U+F8FF: font-specific glyphs (often bullets)
    arabic_presentation_forms: int  # U+FB50-U+FDFF, U+FE70-U+FEFF: shaped, not logical, Arabic
    status: PageStatus


class ExtractionQuality(BaseModel):
    document_id: str
    pages: list[PageQuality]

    @property
    def flagged_pages(self) -> dict[int, PageStatus]:
        return {p.page_number: p.status for p in self.pages if p.status != "ok"}


def assess_page(page_number: int, text: str) -> PageQuality:
    letters = latin = arabic = fffd = pua = pres = 0
    for ch in text:
        code = ord(ch)
        if ch == "�":
            fffd += 1
        elif 0xE000 <= code <= 0xF8FF:
            pua += 1
        elif 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF:
            pres += 1
        if ch.isalpha():
            letters += 1
            if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F:
                arabic += 1
            elif unicodedata.name(ch, "").startswith("LATIN"):
                latin += 1
    if not text.strip():
        status: PageStatus = "empty"
    elif fffd or pres:
        status = "suspect_characters"
    elif letters < LOW_TEXT_CHARS:
        status = "low_text"
    else:
        status = "ok"
    return PageQuality(
        page_number=page_number,
        chars=len(text),
        letters=letters,
        latin_letters=latin,
        arabic_letters=arabic,
        replacement_chars=fffd,
        private_use_chars=pua,
        arabic_presentation_forms=pres,
        status=status,
    )


def assess_extraction(extracted: ExtractedDocument) -> ExtractionQuality:
    return ExtractionQuality(
        document_id=extracted.document.document_id,
        pages=[assess_page(p.page_number, p.text) for p in extracted.pages],
    )
