"""Adversarial cases for evidence and citation validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.errors import EvidenceValidationError
from app.models.schemas import Evidence, Inference, InvestigationReport, Provision, RegulatoryRelationship
from app.services.document_parser import extract_pdf_bytes
from app.services.evidence import evidence_from_span, locate_quote, verify_evidence, verify_report_evidence

PAGES_A = ["Article 1\nLes banques doivent declarer.", "Article 2\nLa circulaire n 2018-06 est abrogee."]
PAGES_B = ["Article 1\nLes banques doivent declarer.", "Article 2\nTexte different."]
WHEN = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.fixture
def doc_a(make_pdf):
    return extract_pdf_bytes(make_pdf(PAGES_A), filename="a.pdf")


@pytest.fixture
def doc_b(make_pdf):
    return extract_pdf_bytes(make_pdf(PAGES_B), filename="b.pdf")


def _ev_like(ev: Evidence, **changes) -> Evidence:
    """A freshly validated Evidence (new, consistent ID) with altered fields."""
    data = ev.model_dump(exclude={"evidence_id"}) | changes
    return Evidence(**data)


# Wrong page -----------------------------------------------------------------

def test_wrong_page_valid_schema_is_rejected(doc_a) -> None:
    ev = locate_quote(doc_a.pages[1], "est abrogee")
    with pytest.raises(EvidenceValidationError, match="does not match"):
        verify_evidence(_ev_like(ev, page_number=1), doc_a)


def test_page_beyond_document_is_rejected(doc_a) -> None:
    ev = locate_quote(doc_a.pages[0], "Article 1")
    with pytest.raises(EvidenceValidationError, match="does not exist"):
        verify_evidence(_ev_like(ev, page_number=99), doc_a)


def test_quote_present_only_on_other_page_cannot_be_located(doc_a) -> None:
    with pytest.raises(EvidenceValidationError, match="not found"):
        locate_quote(doc_a.pages[0], "abrogee")


# Nonexistent quote ---------------------------------------------------------

@pytest.mark.parametrize("quote", ["est abrogee par la BCT", "ARTICLE 1", "Article  1", "", "   "])
def test_nonexistent_or_paraphrased_quote_is_rejected(doc_a, quote: str) -> None:
    with pytest.raises(EvidenceValidationError):
        locate_quote(doc_a.pages[1], quote)


def test_fabricated_quote_with_plausible_offsets_is_rejected(doc_a) -> None:
    real = locate_quote(doc_a.pages[1], "est abrogee")
    forged = _ev_like(real, quote="est modifie")  # same length, same offsets
    with pytest.raises(EvidenceValidationError, match="does not match"):
        verify_evidence(forged, doc_a)


# Forged / stale IDs (validator bypass) -------------------------------------

def test_model_copy_with_stale_id_is_rejected(doc_a) -> None:
    real = locate_quote(doc_a.pages[0], "Article 1")
    # Offsets/quote kept consistent with page 2, but evidence_id is still page 1's.
    stale = real.model_copy(update={"page_number": 2})
    with pytest.raises(EvidenceValidationError, match="schema validation"):
        verify_evidence(stale, doc_a)


def test_model_construct_forged_id_is_rejected(doc_a) -> None:
    real = locate_quote(doc_a.pages[0], "Article 1")
    forged = Evidence.model_construct(**(real.model_dump() | {"evidence_id": "ev_0000000000000000"}))
    with pytest.raises(EvidenceValidationError, match="schema validation"):
        verify_evidence(forged, doc_a)


# Duplicate IDs -------------------------------------------------------------

def test_duplicate_citations_rejected_in_inference_and_relationship() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        Inference(statement="s", model_id="m", supporting_evidence_ids=["ev_1", "ev_1"])
    with pytest.raises(ValidationError, match="duplicate"):
        RegulatoryRelationship(
            source_provision_id="prov_a",
            target_provision_id="prov_b",
            relation_type="amends",
            basis="extracted",
            evidence_ids=["ev_1", "ev_1"],
        )


def test_duplicate_document_ids_rejected_in_report(doc_a) -> None:
    doc_id = doc_a.document.document_id
    with pytest.raises(ValidationError, match="duplicate"):
        InvestigationReport(question="q", generated_at=WHEN, document_ids=[doc_id, doc_id])


# Altered PDF bytes ---------------------------------------------------------

def test_evidence_does_not_verify_against_altered_pdf(make_pdf, doc_a) -> None:
    ev = locate_quote(doc_a.pages[1], "est abrogee")
    altered = extract_pdf_bytes(
        make_pdf(["Article 1\nLes banques doivent declarer.", "Article 2\nLa circulaire n 2018-06 est maintenue."]),
        filename="a.pdf",
    )
    assert altered.document.document_id != doc_a.document.document_id
    with pytest.raises(EvidenceValidationError, match="different document"):
        verify_evidence(ev, altered)


def test_metadata_only_change_still_changes_document_id(make_pdf, doc_a) -> None:
    """IDs bind to exact bytes: same text, different bytes => evidence must be re-derived."""
    retitled = extract_pdf_bytes(make_pdf(PAGES_A, title="other"), filename="a.pdf")
    assert [p.text for p in retitled.pages] == [p.text for p in doc_a.pages]
    assert retitled.document.document_id != doc_a.document.document_id
    with pytest.raises(EvidenceValidationError, match="different document"):
        verify_evidence(locate_quote(doc_a.pages[0], "Article 1"), retitled)


# Invalid character offsets ---------------------------------------------------

@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 5), (5, 5), (6, 5), (0, 10_000)],
)
def test_invalid_spans_are_rejected(doc_a, start: int, end: int) -> None:
    with pytest.raises(EvidenceValidationError, match="outside"):
        evidence_from_span(doc_a.pages[0], start, end)


def test_negative_offset_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        Evidence(document_id="doc_x", page_number=1, char_start=-1, char_end=0, quote="a")


def test_shifted_offsets_are_rejected(doc_a) -> None:
    real = locate_quote(doc_a.pages[1], "est abrogee")
    shifted = _ev_like(real, char_start=real.char_start + 1, char_end=real.char_end + 1)
    with pytest.raises(EvidenceValidationError, match="does not match"):
        verify_evidence(shifted, doc_a)


def test_offsets_past_end_of_page_are_rejected(doc_a) -> None:
    real = locate_quote(doc_a.pages[0], "Article 1")
    n = len(doc_a.pages[0].text)
    past = _ev_like(real, char_start=n, char_end=n + len(real.quote))
    with pytest.raises(EvidenceValidationError, match="does not match"):
        verify_evidence(past, doc_a)


# Cross-document references ---------------------------------------------------

def test_evidence_from_one_document_fails_against_another(doc_a, doc_b) -> None:
    ev = locate_quote(doc_a.pages[0], "Article 1")  # identical text exists in doc_b
    with pytest.raises(EvidenceValidationError, match="different document"):
        verify_evidence(ev, doc_b)


def test_relabelled_document_id_fails(doc_a, doc_b) -> None:
    ev = locate_quote(doc_a.pages[1], "est abrogee")
    relabelled = _ev_like(ev, document_id=doc_b.document.document_id)
    with pytest.raises(EvidenceValidationError, match="does not match"):
        verify_evidence(relabelled, doc_b)


def test_report_evidence_verified_against_correct_sources(doc_a, doc_b) -> None:
    ev_a = locate_quote(doc_a.pages[1], "est abrogee")
    ev_b = locate_quote(doc_b.pages[1], "Texte different")
    report = InvestigationReport(
        question="q",
        generated_at=WHEN,
        document_ids=[doc_a.document.document_id, doc_b.document.document_id],
        evidence=[ev_a, ev_b],
    )
    sources = {d.document.document_id: d for d in (doc_a, doc_b)}
    verify_report_evidence(report, sources)

    with pytest.raises(EvidenceValidationError, match="not available"):
        verify_report_evidence(report, {doc_a.document.document_id: doc_a})
    swapped = {doc_a.document.document_id: doc_b, doc_b.document.document_id: doc_a}
    with pytest.raises(EvidenceValidationError):
        verify_report_evidence(report, swapped)


def test_report_rejects_citation_to_evidence_outside_report(doc_a) -> None:
    ev = locate_quote(doc_a.pages[1], "est abrogee")
    inf = Inference(statement="s", model_id="m", supporting_evidence_ids=[ev.evidence_id])
    with pytest.raises(ValidationError, match="unresolved evidence"):
        InvestigationReport(
            question="q", generated_at=WHEN, document_ids=[doc_a.document.document_id], inferences=[inf]
        )


# Known gap (docs/SPRINT2_REQUIREMENTS.md, R2) ---------------------------------

def test_known_gap_real_quote_supporting_unrelated_relationship_is_accepted(doc_a) -> None:
    """Quote verification proves existence only, not support or provision association.

    This pins current behaviour so the gap stays visible; when R2 is implemented
    this test must be changed to expect rejection.
    """
    doc_id = doc_a.document.document_id
    unrelated_quote = locate_quote(doc_a.pages[0], "Les banques doivent declarer.")
    art1 = Provision(document_id=doc_id, label="Article 1", text="x", page_start=1, page_end=1)
    art2 = Provision(document_id=doc_id, label="Article 2", text="y", page_start=2, page_end=2)
    rel = RegulatoryRelationship(
        source_provision_id=art2.provision_id,
        target_provision_id=art1.provision_id,
        relation_type="repeals",
        basis="extracted",
        evidence_ids=[unrelated_quote.evidence_id],  # says nothing about repeal
    )
    report = InvestigationReport(
        question="q",
        generated_at=WHEN,
        document_ids=[doc_id],
        provisions=[art1, art2],
        evidence=[unrelated_quote],
        relationships=[rel],
    )
    verify_report_evidence(report, {doc_id: doc_a})  # passes today: existence only
