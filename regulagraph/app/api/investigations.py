"""Replay-only demo API (Sprint 3, PM decision D1: no browser-accessible paid inference).

GET  /api/scenarios                 curated scenarios with a recorded live run
POST /api/scenarios/{id}/replay     replay the recorded Muse output through the real pipeline
GET  /api/evaluation                current scorer (v2) results and structural probe totals

Responses never contain full provision text (D3): only exact evidence
quotations, labels, page references, character offsets and official PDF links.
Every relationship is marked AI-generated, candidate and unconfirmed. Source
text verification (the quote is at these offsets, inside this provision) is
reported separately from the model's interpretation, which is never verified.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.redaction import configured_secrets, redact_obj
from app.services import replay as replay_service
from app.services.evaluation import load_cases
from app.services.investigator import InvestigationOutcome

router = APIRouter(prefix="/api")

REPLAY_NOTICE = "Replay of a recorded Muse response. This is not live inference: no model was called."
CANDIDATE_NOTICE = ("AI-generated candidate relationship. Unconfirmed: not reviewed by a person and not a legal "
                    "determination.")
VERIFIED_MEANING = ("Verified source text: this exact quotation was found at these character offsets of the "
                    "extracted page text, inside the cited provision. This proves location only, not meaning.")
INTERPRETATION_MEANING = "Model interpretation. Not verified by RegulaGraph."

State = Literal["ok", "empty", "truncated", "refused", "content_filter", "malformed", "provider_error",
                "disabled", "invalid_request"]


class ScenarioOut(BaseModel):
    id: str
    title: str
    question: str
    primary: bool
    documents: list[str]


class DocumentOut(BaseModel):
    document_id: str
    reference: str | None
    title: str | None
    publication_date: str | None
    source_url: str | None


class ProvisionOut(BaseModel):
    provision_id: str
    document_id: str
    kind: str
    label: str
    page_start: int
    page_end: int


class EvidenceOut(BaseModel):
    evidence_id: str
    document_id: str
    page_number: int
    char_start: int
    char_end: int
    quote: str
    source_text_verified: bool
    pdf_url: str | None


class InterpretationOut(BaseModel):
    text: str
    model_id: str
    verified: Literal[False] = False
    meaning: str = INTERPRETATION_MEANING


class RelationshipOut(BaseModel):
    relationship_id: str
    relation_type: str
    source_provision_id: str
    target_kind: Literal["provision", "document"]
    target_id: str
    status: Literal["candidate"]
    confirmed: Literal[False] = False
    ai_generated: Literal[True] = True
    basis: str
    notice: str = CANDIDATE_NOTICE
    target_mentioned_in_quote: bool
    evidence: list[EvidenceOut]
    interpretation: InterpretationOut | None


class RecordingOut(BaseModel):
    file: str
    recorded_at: datetime
    model_id: str
    response_id: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: float | None


EVALUATION_MEANING = ("Computed by the current scorer from this replay (content digest above), not copied from "
                      "stored labels. 'Validated' means the answer matched the author's annotation; it is not a "
                      "legal or semantic verification of the relationship.")


class ReplayEvaluationOut(BaseModel):
    case_id: str
    correct: bool
    validated: bool
    verdicts: list[str]
    errors: list[str]
    scoring_version: int
    content_digest: str
    recording: str
    meaning: str = EVALUATION_MEANING


class ReplayOut(BaseModel):
    mode: Literal["replay"] = "replay"
    notice: str = REPLAY_NOTICE
    verified_meaning: str = VERIFIED_MEANING
    scenario: ScenarioOut
    recording: RecordingOut
    state: State
    documents: list[DocumentOut]
    provisions: list[ProvisionOut]
    relationships: list[RelationshipOut]
    rejected: list[dict[str, Any]]
    report_id: str
    content_digest: str
    evaluation: ReplayEvaluationOut


def _scenario_out(sc: replay_service.Scenario) -> ScenarioOut:
    case = {c.id: c for c in load_cases().cases}[sc.id]
    return ScenarioOut(id=sc.id, title=sc.title, question=case.question, primary=sc.primary, documents=case.documents)


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios() -> list[ScenarioOut]:
    return [_scenario_out(sc) for sc in replay_service.SCENARIOS]


@router.post("/scenarios/{scenario_id}/replay", response_model=ReplayOut)
def replay_scenario(scenario_id: str) -> ReplayOut:
    settings = get_settings()
    result = replay_service.replay(scenario_id, settings)
    out = to_replay_out(result)
    # Defence in depth: nothing that leaves the API may carry a configured credential.
    return ReplayOut.model_validate(redact_obj(out.model_dump(mode="json"), configured_secrets(settings)))


def to_replay_out(result: replay_service.ReplayResult) -> ReplayOut:
    outcome = result.outcome
    mc = outcome.model_call
    usage = mc.usage
    rec = result.recording
    return ReplayOut(
        scenario=_scenario_out(result.scenario),
        recording=RecordingOut(
            file=rec.file, recorded_at=rec.recorded_at, model_id=mc.model_id, response_id=mc.response_id,
            finish_reason=mc.finish_reason,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            latency_ms=mc.latency_ms,
        ),
        **report_parts(outcome),
        # Bound to this verified replay: replay() recomputed it and rejected any stored label that disagrees.
        evaluation=ReplayEvaluationOut(**result.evaluation),
    )


def report_parts(outcome: InvestigationOutcome) -> dict[str, Any]:
    """The evidence-first view of a finalized investigation, shared by replay and live responses."""
    report = outcome.finalized.report()
    view = replay_service.corpus_view(get_settings())
    sources = {d.document.document_id: d.document for d in view.docs.values()}
    involved_docs = list(dict.fromkeys(
        [p.document_id for p in report.provisions]
        + [r.target_document_id for r in report.relationships if r.target_document_id]))
    evidence = {e.evidence_id: e for e in report.evidence}
    inferences = {i.inference_id: i for i in report.inferences}
    cited = {r.source_provision_id for r in report.relationships} | {
        r.target_provision_id for r in report.relationships if r.target_provision_id}

    def pdf_url(doc_id: str, page: int | None = None) -> str | None:
        url = sources[doc_id].source_url
        return None if url is None else f"{url}#page={page}" if page else str(url)

    relationships = []
    for r in report.relationships:
        inf = inferences.get(r.inference_id) if r.inference_id else None
        relationships.append(RelationshipOut(
            relationship_id=r.relationship_id,
            relation_type=r.relation_type.value,
            source_provision_id=r.source_provision_id,
            target_kind="provision" if r.target_provision_id else "document",
            target_id=r.target_id,
            status=r.status,
            basis=r.basis,
            target_mentioned_in_quote=r.lexical_match is not None,
            evidence=[EvidenceOut(
                evidence_id=e.evidence_id, document_id=e.document_id, page_number=e.page_number,
                char_start=e.char_start, char_end=e.char_end, quote=e.quote,
                source_text_verified=True,  # finalize_report re-verified it against the source; else it would fail
                pdf_url=pdf_url(e.document_id, e.page_number),
            ) for e in (evidence[i] for i in r.evidence_ids)],
            interpretation=InterpretationOut(text=inf.statement, model_id=inf.model_id) if inf else None,
        ))

    mc = outcome.model_call
    state: State = mc.outcome if mc.outcome != "ok" else ("ok" if relationships else "empty")
    return dict(
        state=state,
        documents=[DocumentOut(
            document_id=d, reference=sources[d].reference, title=sources[d].title,
            publication_date=sources[d].publication_date.isoformat() if sources[d].publication_date else None,
            source_url=pdf_url(d),
        ) for d in involved_docs],
        provisions=[ProvisionOut(
            provision_id=p.provision_id, document_id=p.document_id, kind=p.kind, label=" ".join(p.label.split()),
            page_start=p.page_start, page_end=p.page_end,
        ) for p in report.provisions if p.provision_id in cited],
        relationships=relationships,
        rejected=[{"reason": x.reason, "detail": x.detail} for x in outcome.rejected],
        report_id=report.report_id,
        content_digest=outcome.finalized.content_digest,
    )


@router.get("/evaluation")
def evaluation() -> dict[str, Any]:
    try:
        return _evaluation()
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        # Malformed stored results are a controlled 503, never a 500.
        raise replay_service.DemoUnavailable(
            f"stored evaluation results are malformed; {replay_service.RESTORE_HINT}") from exc


def _evaluation() -> dict[str, Any]:
    summary = replay_service.current_summary()
    structural_files = sorted(Path(replay_service.RESULTS_DIR).glob("structural_*.json"))
    structural = json.loads(structural_files[-1].read_text(encoding="utf-8"))["summary"] if structural_files else None
    latest = summary.get("latest_run_per_case", {})
    calls = {(c["file"], c["case_id"]): c for c in summary.get("calls", [])}
    cases = []
    for case in load_cases().cases:
        run = latest.get(case.id)
        call = calls.get((run["file"], case.id)) if run else None
        if call is None:
            status = "not_run"
        elif call["validated"]:
            status = "validated"
        elif call["model_outcome"] == "truncated":
            status = "truncated"
        else:
            status = "not_validated"
        cases.append({
            "id": case.id, "phenomenon": case.phenomenon, "polarity": case.polarity, "status": status,
            "model_outcome": call["model_outcome"] if call else None,
            "finish_reason": call["finish_reason"] if call else None,
            "completion_tokens": call["completion_tokens"] if call else None,
            "reasoning_tokens": call["reasoning_tokens"] if call else None,
            "errors": call["errors"] if call else [],
            "file": run["file"] if run else None,
        })
    return {
        "scoring_version": summary.get("scoring_version"),
        "meaning": ("'Validated' means the recorded model answer matched the author's annotation for that case, "
                    "with every required provision in the prompt. It is not a legal verification of any "
                    "relationship, and the annotations have not been reviewed by a domain expert."),
        "cases": cases,
        "structural": None if structural is None else {
            **{k: structural[k] for k in ("probes", "accept_total", "reject_total", "undetectable_total",
                                          "undetectable_published", "retrieval_source_hits", "retrieval_target_hits",
                                          "retrieval_target_total", "retrieval_cases") if k in structural},
            "undetectable_meaning": (
                "Hand-made proposals with a wrong relation type, or a forbidden link backed by a real quote, "
                "pass every structural check and are published as candidates. Structural validation cannot "
                "detect semantic errors; only review can."),
        },
    }
