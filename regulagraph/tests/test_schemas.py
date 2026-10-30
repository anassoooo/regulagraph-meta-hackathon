from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core import ids
from app.models.schemas import (
    Document,
    Evidence,
    Inference,
    InvestigationReport,
    Provision,
    RegulatoryRelationship,
)

SHA = "a" * 64
DOC_ID = ids.document_id(SHA)
WHEN = datetime(2026, 10, 27, tzinfo=UTC)


def _evidence(quote: str = "Article 2 est abroge", start: int = 10) -> Evidence:
    return Evidence(document_id=DOC_ID, page_number=1, char_start=start, char_end=start + len(quote), quote=quote)


def _provisions() -> tuple[Provision, Provision]:
    a = Provision(document_id=DOC_ID, label="Article 1", text="Texte 1", page_start=1, page_end=1)
    b = Provision(document_id=DOC_ID, label="Article 2", text="Texte 2", page_start=1, page_end=2)
    return a, b


# Document -----------------------------------------------------------------

def test_document_id_derived_from_hash() -> None:
    doc = Document(sha256=SHA, filename="c.pdf", page_count=3)
    assert doc.document_id == DOC_ID
    assert doc.issuer == "Banque Centrale de Tunisie"


def test_document_rejects_spoofed_id_and_bad_hash() -> None:
    with pytest.raises(ValidationError, match="content-derived"):
        Document(document_id="doc_fake", sha256=SHA, filename="c.pdf", page_count=1)
    with pytest.raises(ValidationError):
        Document(sha256="xyz", filename="c.pdf", page_count=1)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Document(sha256=SHA, filename="c.pdf", page_count=1, confidence=0.9)


# Evidence -----------------------------------------------------------------

def test_evidence_id_is_deterministic_and_round_trips() -> None:
    ev = _evidence()
    assert ev.evidence_id == _evidence().evidence_id
    assert ev.evidence_id != _evidence(start=11).evidence_id
    assert Evidence.model_validate_json(ev.model_dump_json()) == ev


@pytest.mark.parametrize(
    "kwargs",
    [
        {"char_start": 0, "char_end": 3, "quote": "abcd"},  # length mismatch
        {"char_start": 0, "char_end": 3, "quote": "   "},  # whitespace only
        {"char_start": 0, "char_end": 1, "quote": "a", "page_number": 0},  # pages are 1-based
        {"char_start": 0, "char_end": 1, "quote": "a", "kind": "model_inference"},  # cannot relabel
    ],
)
def test_invalid_evidence(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        Evidence(document_id=DOC_ID, **{"page_number": 1, **kwargs})


# Provision ----------------------------------------------------------------

def test_provision_page_range_validated() -> None:
    with pytest.raises(ValidationError, match="page_end"):
        Provision(document_id=DOC_ID, label="Article 1", text="x", page_start=3, page_end=2)


# Inference ----------------------------------------------------------------

def test_inference_requires_evidence_and_model() -> None:
    with pytest.raises(ValidationError):
        Inference(statement="X amends Y", model_id="muse-spark-1.3", supporting_evidence_ids=[])
    with pytest.raises(ValidationError):
        Inference(statement="X amends Y", model_id="", supporting_evidence_ids=["ev_1"])


def test_inference_id_independent_of_citation_order() -> None:
    a = Inference(statement="s", model_id="m", supporting_evidence_ids=["ev_1", "ev_2"])
    b = Inference(statement="s", model_id="m", supporting_evidence_ids=["ev_2", "ev_1"])
    assert a.inference_id == b.inference_id


# RegulatoryRelationship ---------------------------------------------------

def test_relationship_basis_rules() -> None:
    a, b = _provisions()
    base = {
        "source_provision_id": b.provision_id,
        "target_provision_id": a.provision_id,
        "relation_type": "repeals",
        "evidence_ids": ["ev_1"],
    }
    assert RegulatoryRelationship(basis="extracted", **base).relationship_id.startswith("rel_")
    with pytest.raises(ValidationError, match="inference_id"):
        RegulatoryRelationship(basis="inferred", **base)
    with pytest.raises(ValidationError, match="must not reference"):
        RegulatoryRelationship(basis="extracted", inference_id="inf_1", **base)
    with pytest.raises(ValidationError, match="itself"):
        RegulatoryRelationship(basis="extracted", **{**base, "target_provision_id": b.provision_id})
    with pytest.raises(ValidationError):
        RegulatoryRelationship(basis="extracted", **{**base, "evidence_ids": []})
    with pytest.raises(ValidationError):
        RegulatoryRelationship(basis="extracted", **{**base, "relation_type": "vibes"})


# InvestigationReport ------------------------------------------------------

def _report(**overrides) -> InvestigationReport:
    a, b = _provisions()
    ev = _evidence()
    inf = Inference(statement="Article 2 repeals Article 1", model_id="m", supporting_evidence_ids=[ev.evidence_id])
    rel = RegulatoryRelationship(
        source_provision_id=b.provision_id,
        target_provision_id=a.provision_id,
        relation_type="repeals",
        basis="inferred",
        evidence_ids=[ev.evidence_id],
        inference_id=inf.inference_id,
    )
    values = {
        "question": "What does Article 2 change?",
        "generated_at": WHEN,
        "document_ids": [DOC_ID],
        "provisions": [a, b],
        "evidence": [ev],
        "inferences": [inf],
        "relationships": [rel],
    }
    values.update(overrides)
    return InvestigationReport(**values)


def test_valid_report_round_trips() -> None:
    report = _report()
    assert report.report_id == _report().report_id
    assert InvestigationReport.model_validate_json(report.model_dump_json()) == report


def test_report_rejects_citation_to_missing_evidence() -> None:
    with pytest.raises(ValidationError, match="unresolved evidence"):
        _report(evidence=[])


def test_report_rejects_evidence_from_unlisted_document() -> None:
    with pytest.raises(ValidationError, match="unresolved document"):
        _report(document_ids=[ids.document_id("b" * 64)])


def test_report_rejects_relationship_to_unknown_provision() -> None:
    a, _ = _provisions()
    with pytest.raises(ValidationError, match="unresolved provision"):
        _report(provisions=[a])


def test_report_rejects_duplicates() -> None:
    ev = _evidence()
    with pytest.raises(ValidationError, match="duplicate"):
        _report(evidence=[ev, ev])
