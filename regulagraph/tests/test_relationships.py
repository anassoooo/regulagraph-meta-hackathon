"""R2: relationship evidence must lie in the related provisions (docs/SPRINT2_REQUIREMENTS.md)."""

from datetime import UTC, datetime

import pytest

from app.core import ids
from app.core.errors import EvidenceValidationError, PDFExtractionError
from app.models.schemas import Document, Inference, InvestigationReport, RegulatoryRelationship
from app.services.corpus import CorpusEntry, attach_metadata
from app.services.document_parser import extract_pdf_bytes
from app.services.evidence import evidence_from_span
from app.services.provisions import locate_in_provision, segment_provisions
from app.services.relationships import (
    document_reference_pattern,
    find_lexical_match,
    provision_reference_pattern,
)
from app.services.report import finalize_report

WHEN = datetime(2026, 10, 30, tzinfo=UTC)
NEW = [
    "CIRCULAIRE N 2026-99\nVu la circulaire n 2018-16;",
    "Article 1 : Objet de la circulaire.\nArticle 2 : La presente circulaire abroge la circulaire n 2018-16.\n"
    "Article 3 : Elle entre en vigueur sans prejudice de l article 1.",
]
OLD = ["Article 1 : Ancien objet.\nArticle 2 : Ancienne regle."]


def _entry(key: str, ref: str, data: bytes) -> CorpusEntry:
    return CorpusEntry(
        key=key, reference=ref, title=ref, publication_date="2026-01-01", language="fr", role="investigation",
        source_url="https://example.org/x.pdf", filename=f"{key}.pdf", sha256=ids.sha256_hex(data),
        bytes=len(data), retrieved_on="2026-10-30", pages=1,
    )


@pytest.fixture
def corpus(make_pdf):
    new_pdf, old_pdf = make_pdf(NEW), make_pdf(OLD)
    new = attach_metadata(extract_pdf_bytes(new_pdf, filename="n.pdf"), _entry("new", "Circulaire n°2026-99", new_pdf))
    old = attach_metadata(extract_pdf_bytes(old_pdf, filename="o.pdf"), _entry("old", "Circulaire n°2018-16", old_pdf))
    return new, old


def _art(provs, label):
    return next(p for p in provs if p.label == label and p.kind == "article")


def _report(new, old, rel_kwargs, evidence, inference_evidence=None, extra_provisions=()):
    new_provs, old_provs = segment_provisions(new), segment_provisions(old)
    provisions = list({p.provision_id: p for p in [*new_provs, *old_provs, *extra_provisions]}.values())
    inf = Inference(
        statement="proposed by a model",
        model_id="m",
        supporting_evidence_ids=[e.evidence_id for e in (inference_evidence or evidence)],
    )
    all_evidence = list({e.evidence_id: e for e in [*evidence, *(inference_evidence or [])]}.values())
    rel = RegulatoryRelationship(
        relation_type="repeals",
        basis="inferred",
        inference_id=inf.inference_id,
        evidence_ids=[e.evidence_id for e in evidence],
        **rel_kwargs,
    )
    return InvestigationReport(
        question="Which circular is repealed?",
        generated_at=WHEN,
        document_ids=[new.document.document_id, old.document.document_id],
        provisions=provisions,
        evidence=all_evidence,
        inferences=[inf],
        relationships=[rel],
    )


def _sources(*docs):
    return {d.document.document_id: d for d in docs}


def test_evidence_inside_source_provision_is_accepted(corpus) -> None:
    new, old = corpus
    art2 = _art(segment_provisions(new), "Article 2")
    ev = locate_in_provision(art2, "abroge la circulaire n 2018-16", new)
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": old.document.document_id}, [ev])
    final = finalize_report(report, _sources(new, old))
    assert final.report().relationships[0].status == "candidate"


def test_evidence_from_a_third_provision_is_rejected(corpus) -> None:
    new, old = corpus
    provs = segment_provisions(new)
    art2, art3 = _art(provs, "Article 2"), _art(provs, "Article 3")
    in_source = locate_in_provision(art2, "abroge", new)
    elsewhere = locate_in_provision(art3, "sans prejudice", new)
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": old.document.document_id}, [in_source, elsewhere])
    with pytest.raises(EvidenceValidationError, match="outside the source and target"):
        finalize_report(report, _sources(new, old))


def test_evidence_from_a_different_document_is_rejected(corpus) -> None:
    new, old = corpus
    art2 = _art(segment_provisions(new), "Article 2")
    other_doc = locate_in_provision(_art(segment_provisions(old), "Article 2"), "Ancienne regle", old)
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": old.document.document_id}, [other_doc])
    with pytest.raises(EvidenceValidationError):
        finalize_report(report, _sources(new, old))


def test_evidence_straddling_a_provision_boundary_is_rejected(corpus) -> None:
    new, old = corpus
    art2 = _art(segment_provisions(new), "Article 2")
    page = new.pages[1]
    seg = art2.segments[0]
    straddling = evidence_from_span(page, seg.char_end - 5, seg.char_end + 10)  # runs into Article 3
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": old.document.document_id}, [straddling])
    with pytest.raises(EvidenceValidationError):
        finalize_report(report, _sources(new, old))


def test_preamble_citation_is_not_inside_the_article(corpus) -> None:
    new, old = corpus
    provs = segment_provisions(new)
    preamble = next(p for p in provs if p.kind == "preamble")
    art2 = _art(provs, "Article 2")
    visa = locate_in_provision(preamble, "Vu la circulaire n 2018-16", new)
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": old.document.document_id}, [visa])
    with pytest.raises(EvidenceValidationError):
        finalize_report(report, _sources(new, old))


def test_inference_citing_evidence_missing_from_relationship_is_rejected(corpus) -> None:
    new, old = corpus
    provs = segment_provisions(new)
    art2 = _art(provs, "Article 2")
    ev = locate_in_provision(art2, "abroge", new)
    extra = locate_in_provision(_art(provs, "Article 1"), "Objet", new)
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": old.document.document_id}, [ev], inference_evidence=[ev, extra])
    with pytest.raises(EvidenceValidationError, match="inference cites evidence"):
        finalize_report(report, _sources(new, old))


def test_relationship_to_own_document_is_rejected(corpus) -> None:
    new, old = corpus
    art2 = _art(segment_provisions(new), "Article 2")
    ev = locate_in_provision(art2, "abroge", new)
    report = _report(new, old, {"source_provision_id": art2.provision_id,
                                "target_document_id": new.document.document_id}, [ev])
    with pytest.raises(EvidenceValidationError, match="own document"):
        finalize_report(report, _sources(new, old))


# Lexical rule -------------------------------------------------------------

@pytest.mark.parametrize(
    "text, matches",
    [("circulaire n°2018-16 du", True), ("n°2018 -16", True), ("2018-016", True),
     ("2018-160", False), ("12018-16", False), ("2018-61", False), ("2016-18", False)],
)
def test_document_reference_pattern(text, matches) -> None:
    assert bool(document_reference_pattern("Circulaire n°2018-16").search(text)) is matches


def _extracted_report(new, old, quote_text, target_label="Article 1"):
    provs = segment_provisions(new)
    source, target = _art(provs, "Article 3"), _art(provs, target_label)
    ev = locate_in_provision(source, quote_text, new)
    match = find_lexical_match(ev, provision_reference_pattern(target))
    rel = RegulatoryRelationship(
        source_provision_id=source.provision_id, target_provision_id=target.provision_id,
        relation_type="references", basis="extracted", evidence_ids=[ev.evidence_id],
        lexical_match=match.model_dump() if match else {"evidence_id": ev.evidence_id, "match_start": 0, "match_end": 3},
    )
    return InvestigationReport(
        question="q", generated_at=WHEN, document_ids=[new.document.document_id], provisions=provs,
        evidence=[ev], relationships=[rel],
    )


def test_extracted_relationship_whose_quote_names_the_target_is_accepted(corpus) -> None:
    new, old = corpus
    report = _extracted_report(new, old, "sans prejudice de l article 1")
    finalize_report(report, _sources(new))


def test_extracted_relationship_whose_quote_never_names_the_target_is_rejected(corpus) -> None:
    new, old = corpus
    report = _extracted_report(new, old, "Elle entre en vigueur")  # no mention of Article 1
    with pytest.raises(EvidenceValidationError, match="does not name the target"):
        finalize_report(report, _sources(new))


def test_lexical_match_is_not_semantic_support(corpus) -> None:
    """'sans préjudice de l'article 1' names Article 1 but does not repeal it.

    The lexical rule accepts it for any relation type; only the relation type is
    wrong. This pins the documented limit: lexical checks are not entailment.
    """
    new, old = corpus
    report = _extracted_report(new, old, "sans prejudice de l article 1")
    data = report.model_dump()
    data["relationships"][0].update(relation_type="repeals", relationship_id="")
    finalize_report(InvestigationReport.model_validate(data), _sources(new))  # accepted: structure only


def test_document_metadata_must_match_the_entry(corpus, make_pdf) -> None:
    new, _ = corpus
    other = make_pdf(["x"])
    with pytest.raises(PDFExtractionError, match="not of this corpus entry"):
        attach_metadata(new, _entry("x", "Circulaire n°1-1", other))
    assert isinstance(new.document, Document) and new.document.reference == "Circulaire n°2026-99"
