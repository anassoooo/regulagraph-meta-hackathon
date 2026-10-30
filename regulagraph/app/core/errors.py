"""Structured application errors.

Every error carries a stable machine-readable ``code`` and a human message.
``details`` must never contain secrets or raw request headers.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class RegulaGraphError(Exception):
    code: str = "regulagraph_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class PDFExtractionError(RegulaGraphError):
    code = "pdf_extraction_failed"
    http_status = 422


class EvidenceValidationError(RegulaGraphError):
    code = "evidence_invalid"
    http_status = 422


class ReportValidationError(RegulaGraphError):
    code = "report_invalid"
    http_status = 422


class PublicationBlockedError(RegulaGraphError):
    """A report contains content that the fail-closed publication rules refuse."""

    code = "publication_blocked"
    http_status = 422


class InvalidModelRequestError(RegulaGraphError):
    """Rejected locally, before any request is built or sent."""

    code = "invalid_model_request"
    http_status = 400


class InferenceDisabledError(RegulaGraphError):
    """Raised before any network call when paid inference is not explicitly enabled."""

    code = "inference_disabled"
    http_status = 503


class ModelProviderError(RegulaGraphError):
    code = "model_provider_error"
    http_status = 502

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            message,
            details={"status_code": status_code, "error_type": error_type, "retryable": retryable},
        )
        self.status_code = status_code
        self.error_type = error_type
        self.retryable = retryable


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RegulaGraphError)
    async def _handle(_: Request, exc: RegulaGraphError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})
