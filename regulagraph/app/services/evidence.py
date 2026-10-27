"""Build and verify evidence against extracted page text.

Quotes proposed by a model are only accepted if they occur verbatim on the
cited page; this is the guard against fabricated citations.
"""

from collections.abc import Mapping

from pydantic import ValidationError

from app.core.errors import EvidenceValidationError
from app.models.schemas import Evidence, ExtractedDocument, ExtractedPage, InvestigationReport


def evidence_from_span(page: ExtractedPage, char_start: int, char_end: int) -> Evidence:
    if not 0 <= char_start < char_end <= len(page.text):
        raise EvidenceValidationError(
            "span is outside the page text",
            details={"page_id": page.page_id, "char_start": char_start, "char_end": char_end},
        )
    return Evidence(
        document_id=page.document_id,
        page_number=page.page_number,
        char_start=char_start,
        char_end=char_end,
        quote=page.text[char_start:char_end],
    )


def locate_quote(page: ExtractedPage, quote: str) -> Evidence:
    """Return evidence for the first verbatim occurrence of ``quote`` on ``page``."""
    start = page.text.find(quote) if quote.strip() else -1
    if start < 0:
        raise EvidenceValidationError("quote not found on cited page", details={"page_id": page.page_id})
    return evidence_from_span(page, start, start + len(quote))


def verify_evidence(evidence: Evidence, extracted: ExtractedDocument) -> None:
    """Raise unless ``evidence`` matches the extracted document text exactly."""
    # Re-run schema validation: ``model_copy(update=...)`` and ``model_construct``
    # bypass validators and could carry a stale or forged evidence_id.
    try:
        Evidence.model_validate(evidence.model_dump())
    except ValidationError as exc:
        raise EvidenceValidationError(
            "evidence fails schema validation", details={"evidence_id": evidence.evidence_id}
        ) from exc
    if evidence.document_id != extracted.document.document_id:
        raise EvidenceValidationError(
            "evidence cites a different document", details={"evidence_id": evidence.evidence_id}
        )
    if evidence.page_number > len(extracted.pages):
        raise EvidenceValidationError(
            "evidence cites a page that does not exist", details={"evidence_id": evidence.evidence_id}
        )
    page = extracted.pages[evidence.page_number - 1]
    if page.text[evidence.char_start : evidence.char_end] != evidence.quote:
        raise EvidenceValidationError(
            "evidence quote does not match page text", details={"evidence_id": evidence.evidence_id}
        )


def verify_report_evidence(report: InvestigationReport, sources: Mapping[str, ExtractedDocument]) -> None:
    """Verify every evidence item in ``report`` against its source text, keyed by document_id.

    ``InvestigationReport`` only checks that references resolve; this checks the
    quotes themselves. Call it before a report leaves the backend.
    """
    for evidence in report.evidence:
        source = sources.get(evidence.document_id)
        if source is None:
            raise EvidenceValidationError(
                "source document not available for verification",
                details={"evidence_id": evidence.evidence_id, "document_id": evidence.document_id},
            )
        verify_evidence(evidence, source)
