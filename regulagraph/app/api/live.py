"""Local live investigation and review API (Sprint 3.5). Disabled unless the server runs in live mode.

GET  /api/live/status                                  whether paid live calls are enabled; budget left
GET  /api/live/documents                               the source documents a user may approve
POST /api/live/investigations                          one paid Muse call over the approved documents
GET  /api/live/investigations/{report_id}/reviews      review records for a live run
POST /api/live/investigations/{report_id}/reviews      append a review record (the report is not changed)

Every request must come from the loopback interface with a local Host header (DNS-rebinding
guard). Every POST must be JSON (no form or "simple" cross-site request), carry an Origin equal
to this server, and not be marked cross-site by the browser. Live inference needs the server in
live mode, a confirmation flag in the request, and budget left in the ledger; see
``app/services/live.py`` for the allowlist, prompt and spending checks.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.investigations import (
    VERIFIED_MEANING,
    DocumentOut,
    ProvisionOut,
    RelationshipOut,
    State,
    report_parts,
)
from app.core.config import get_settings
from app.core.errors import RegulaGraphError
from app.core.redaction import configured_secrets, redact_obj
from app.services import live as live_service
from app.services import replay as replay_service

router = APIRouter(prefix="/api/live")

LIVE_NOTICE = ("Live inference: Meta Muse was called once, with source context from the documents you approved "
               "only. Every relationship is an AI-generated candidate and unconfirmed.")
REVIEW_MEANING = ("A review records a reviewer's opinion beside the immutable report. 'Reviewer accepted' is not a "
                  "legal confirmation: the relationship stays an AI-generated, unconfirmed candidate.")
COST_BASIS = "Estimated from reported token usage and published prices; not a provider invoice."
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1"})
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


class LiveRequestRefused(RegulaGraphError):
    code = "live_request_refused"
    http_status = 403


class UnsupportedMediaType(RegulaGraphError):
    code = "unsupported_media_type"
    http_status = 415


class BudgetOut(BaseModel):
    ceiling_usd: float
    spent_usd: float
    remaining_usd: float
    calls_used: int
    max_calls: int
    basis: str = COST_BASIS


class LiveStatusOut(BaseModel):
    enabled: bool
    reason: str | None
    model_id: str
    max_question_chars: int = live_service.MAX_QUESTION_CHARS
    budget: BudgetOut | None


class LiveDocumentOut(BaseModel):
    document_id: str
    reference: str | None
    title: str | None
    publication_date: str | None


class LiveCallOut(BaseModel):
    model_id: str
    outcome: str
    response_id: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: float | None
    estimated_cost_usd: float | None


class LiveOut(BaseModel):
    mode: Literal["live"] = "live"
    notice: str = LIVE_NOTICE
    verified_meaning: str = VERIFIED_MEANING
    review_meaning: str = REVIEW_MEANING
    question: str
    approved_document_ids: list[str]
    created_at: datetime
    call: LiveCallOut
    state: State
    documents: list[DocumentOut]
    provisions: list[ProvisionOut]
    relationships: list[RelationshipOut]
    rejected: list[dict[str, Any]]
    report_id: str
    content_digest: str
    budget: BudgetOut


class ReviewOut(BaseModel):
    review_id: str
    report_id: str
    content_digest: str
    relationship_id: str
    decision: Literal["accepted", "rejected", "needs_more_evidence"]
    note: str
    reviewed_at: datetime
    relationship_status: Literal["candidate"]
    legal_confirmation: Literal[False]


class ReviewsOut(BaseModel):
    report_id: str
    content_digest: str
    meaning: str = REVIEW_MEANING
    reviews: list[ReviewOut]


# --- request guards ------------------------------------------------------------------

def _host_name(host: str) -> str:
    host = host.strip().lower()
    return host[: host.index("]") + 1] if host.startswith("[") and "]" in host else host.split(":")[0]


def local_only(request: Request) -> None:
    client = request.client.host if request.client else None
    if client not in LOOPBACK_CLIENTS:
        raise LiveRequestRefused("Live mode accepts local requests only.")
    if _host_name(request.headers.get("host", "")) not in LOCAL_HOSTNAMES:
        raise LiveRequestRefused("Live mode accepts requests addressed to 127.0.0.1 or localhost only.")


def same_origin_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise UnsupportedMediaType("Send the request as application/json.")
    if request.headers.get("origin") != f"{request.url.scheme}://{request.headers.get('host', '')}":
        raise LiveRequestRefused("Cross-origin requests are refused.")
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        raise LiveRequestRefused("Cross-site requests are refused.")


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise live_service.InvalidLiveRequest("The request body is not valid JSON.") from exc
    if not isinstance(body, dict):
        raise live_service.InvalidLiveRequest("The request body must be a JSON object.")
    return body


def _redacted(model: type[BaseModel], value: BaseModel) -> Any:
    # Defence in depth: nothing that leaves the API may carry a configured credential.
    return model.model_validate(redact_obj(value.model_dump(mode="json"), configured_secrets(get_settings())))


def _budget_out() -> BudgetOut:
    st = live_service.budget_state(get_settings())
    return BudgetOut(ceiling_usd=st.ceiling_usd, spent_usd=st.spent_usd, remaining_usd=st.remaining_usd,
                     calls_used=st.calls_used, max_calls=st.max_calls)


# --- endpoints ---------------------------------------------------------------------------

@router.get("/status", response_model=LiveStatusOut)
def status(request: Request) -> LiveStatusOut:
    local_only(request)
    settings = get_settings()
    reason = live_service.disabled_reason(settings)
    budget = None
    if reason is None:
        try:
            budget = _budget_out()
        except RegulaGraphError as exc:
            reason = exc.message
    return LiveStatusOut(enabled=reason is None, reason=reason, model_id=settings.meta_model_id, budget=budget)


@router.get("/documents", response_model=list[LiveDocumentOut])
def documents(request: Request) -> list[LiveDocumentOut]:
    local_only(request)
    settings = get_settings()
    live_service.require_enabled(settings)
    view = replay_service.corpus_view(settings)
    out = []
    for key in live_service.live_document_keys(view):
        d = view.docs[key].document
        out.append(LiveDocumentOut(document_id=d.document_id, reference=d.reference, title=d.title,
                                   publication_date=d.publication_date.isoformat() if d.publication_date else None))
    return out


@router.post("/investigations", response_model=LiveOut)
async def investigate_live(request: Request) -> LiveOut:
    local_only(request)
    settings = get_settings()
    live_service.require_enabled(settings)  # before reading the body: a disabled server does nothing
    same_origin_json(request)
    body = await _json_object(request)
    view = replay_service.corpus_view(settings)
    run = await run_in_threadpool(live_service.run_live, settings, view, body.get("question"),
                                  body.get("document_ids"), body.get("confirm_paid_call"))
    mc = run.outcome.model_call
    usage = mc.usage
    out = LiveOut(
        question=run.question,
        approved_document_ids=run.document_ids,
        created_at=run.created_at,
        call=LiveCallOut(
            model_id=mc.model_id, outcome=mc.outcome, response_id=mc.response_id, finish_reason=mc.finish_reason,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            latency_ms=mc.latency_ms, estimated_cost_usd=run.cost_usd,
        ),
        **report_parts(run.outcome),
        budget=_budget_out(),
    )
    return _redacted(LiveOut, out)


@router.get("/investigations/{report_id}/reviews", response_model=ReviewsOut)
def list_reviews(report_id: str, request: Request) -> ReviewsOut:
    local_only(request)
    settings = get_settings()
    live_service.require_enabled(settings)
    run = live_service.load_run(settings, report_id)
    reviews = [ReviewOut.model_validate(r) for r in live_service.reviews_for(settings, report_id)]
    return _redacted(ReviewsOut, ReviewsOut(report_id=report_id, content_digest=run.outcome.finalized.content_digest,
                                            reviews=reviews))


@router.post("/investigations/{report_id}/reviews", response_model=ReviewOut)
async def add_review(report_id: str, request: Request) -> ReviewOut:
    local_only(request)
    settings = get_settings()
    live_service.require_enabled(settings)
    same_origin_json(request)
    body = await _json_object(request)
    record = await run_in_threadpool(live_service.add_review, settings, report_id, body.get("relationship_id"),
                                     body.get("content_digest"), body.get("decision"), body.get("note"))
    return _redacted(ReviewOut, ReviewOut.model_validate(record))
