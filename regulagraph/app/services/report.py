"""Mandatory report finalization.

``finalize_report`` is the only supported way to turn an ``InvestigationReport``
into something publishable. Pydantic models can be mutated after construction
without re-validation (attribute assignment, list append), so finalization:

1. serializes the report and re-validates that snapshot from scratch;
2. re-validates each source extraction the same way (catching mutated pages);
3. applies the fail-closed publication rules (no "confirmed" relationships);
4. verifies every evidence quote against its source page text;
5. returns an immutable ``FinalizedReport`` whose ``content_digest`` binds the
   complete canonical snapshot, findings included.

This proves structural integrity and that quoted passages exist in the
sources. It does not prove that evidence semantically supports any inference
or relationship, or that it lies within the provisions a relationship names.
"""

from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import ValidationError

from app.core.errors import PublicationBlockedError, ReportValidationError
from app.models.schemas import ExtractedDocument, FinalizedReport, InvestigationReport, assert_publishable
from app.services.evidence import verify_report_evidence

M = TypeVar("M", InvestigationReport, ExtractedDocument)


def finalize_report(report: InvestigationReport, sources: Mapping[str, ExtractedDocument]) -> FinalizedReport:
    snapshot = _revalidate(InvestigationReport, report, "report fails re-validation")

    verified_sources: dict[str, ExtractedDocument] = {}
    for doc_id in snapshot.document_ids:
        if doc_id not in sources:
            raise ReportValidationError("source document not provided", details={"document_id": doc_id})
        source = _revalidate(ExtractedDocument, sources[doc_id], "source extraction fails re-validation")
        if source.document.document_id != doc_id:
            raise ReportValidationError("source registered under the wrong document_id", details={"document_id": doc_id})
        verified_sources[doc_id] = source

    try:
        assert_publishable(snapshot)
    except ValueError as exc:
        raise PublicationBlockedError(str(exc)) from exc

    verify_report_evidence(snapshot, verified_sources)

    return FinalizedReport(
        report_id=snapshot.report_id,
        content_digest=snapshot.content_digest(),
        snapshot=snapshot.canonical_snapshot(),
        verified_sources={doc_id: src.provenance.text_digest for doc_id, src in verified_sources.items()},
    )


def _revalidate(model: type[M], obj: M, message: str) -> M:
    """Round-trip through JSON so validators run on the current state, not the constructed one."""
    try:
        return model.model_validate_json(obj.model_dump_json(warnings="error"))
    except ValidationError as exc:
        raise ReportValidationError(message, details={"errors": _error_summary(exc)}) from exc
    except (ValueError, TypeError) as exc:  # serialization of a mutated, wrongly typed field
        raise ReportValidationError(message, details={"errors": [{"msg": type(exc).__name__}]}) from exc


def _error_summary(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": [str(part) for part in err["loc"]], "msg": err["msg"]} for err in exc.errors()]
