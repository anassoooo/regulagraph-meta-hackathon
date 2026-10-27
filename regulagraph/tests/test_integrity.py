"""Regression tests for the PR #3 integrity review (items 1-6)."""

from datetime import UTC, datetime

import httpx
import pypdf
import pytest
from pydantic import ValidationError

from app.core import ids
from app.core.errors import (
    EvidenceValidationError,
    InvalidModelRequestError,
    PublicationBlockedError,
    ReportValidationError,
)
from app.models.schemas import (
    Evidence,
    ExtractedDocument,
    ExtractedPage,
    FinalizedReport,
    Inference,
    InvestigationReport,
    Provision,
    RegulatoryRelationship,
    page_text_digest,
)
from app.services.document_parser import extract_pdf_bytes
from app.services.evidence import locate_quote
from app.services.meta_client import ChatMessage, MetaModelAdapter
from app.services.report import finalize_report

PAGES = ["Article 1\nLes banques doivent declarer.", "Article 2\nLa circulaire n 2018-06 est abrogee."]
WHEN = datetime(2026, 10, 27, tzinfo=UTC)
SEP = "\x1f"


def _legacy_stable_id(prefix: str, *parts: str | int) -> str:
    """The Sprint 1 scheme, kept only to prove the collisions it allowed."""
    return f"{prefix}_{ids.sha256_hex(SEP.join(str(p) for p in parts).encode())[:16]}"


@pytest.fixture
def doc(make_pdf) -> ExtractedDocument:
    return extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")


@pytest.fixture
def report(doc) -> InvestigationReport:
    doc_id = doc.document.document_id
    art1 = Provision(document_id=doc_id, label="Article 1", text="x", page_start=1, page_end=1)
    art2 = Provision(document_id=doc_id, label="Article 2", text="y", page_start=2, page_end=2)
    ev = locate_quote(doc.pages[1], "La circulaire n 2018-06 est abrogee.")
    inf = Inference(statement="Article 2 repeals circular 2018-06", model_id="m", supporting_evidence_ids=[ev.evidence_id])
    rel = RegulatoryRelationship(
        source_provision_id=art2.provision_id,
        target_provision_id=art1.provision_id,
        relation_type="repeals",
        basis="inferred",
        evidence_ids=[ev.evidence_id],
        inference_id=inf.inference_id,
    )
    return InvestigationReport(
        question="What does Article 2 change?",
        generated_at=WHEN,
        document_ids=[doc_id],
        provisions=[art1, art2],
        evidence=[ev],
        inferences=[inf],
        relationships=[rel],
    )


def _sources(doc: ExtractedDocument) -> dict[str, ExtractedDocument]:
    return {doc.document.document_id: doc}


# 1. Canonical ID serialization ---------------------------------------------

def test_legacy_scheme_collided_and_canonical_scheme_does_not() -> None:
    a, b = ("a" + SEP + "b", "c"), ("a", "b" + SEP + "c")
    assert _legacy_stable_id("x", *a) == _legacy_stable_id("x", *b)
    assert ids.stable_id("x", *a) != ids.stable_id("x", *b)


def test_inference_separator_injection_does_not_collide() -> None:
    injected = Inference(statement="s" + SEP + "ev_a", model_id="m", supporting_evidence_ids=["ev_b"])
    honest = Inference(statement="s", model_id="m", supporting_evidence_ids=["ev_a", "ev_b"])
    assert _legacy_stable_id("inf", "m", "s" + SEP + "ev_a", "ev_b") == _legacy_stable_id("inf", "m", "s", "ev_a", "ev_b")
    assert injected.inference_id != honest.inference_id


def test_relationship_separator_injection_does_not_collide() -> None:
    common = {"basis": "extracted", "evidence_ids": ["ev_1"]}
    injected = RegulatoryRelationship(
        source_provision_id="p" + SEP + "references" + SEP + "q", relation_type="amends", target_provision_id="r", **common
    )
    honest = RegulatoryRelationship(
        source_provision_id="p", relation_type="references", target_provision_id="q" + SEP + "amends" + SEP + "r", **common
    )
    assert injected.relationship_id != honest.relationship_id


def test_report_run_id_separator_injection_does_not_collide() -> None:
    d1, d2 = ids.document_id("a" * 64), ids.document_id("b" * 64)
    honest = InvestigationReport(question="q", generated_at=WHEN, document_ids=[d1, d2])
    # A question that embeds the timestamp and a document id must not alias the honest run.
    injected = InvestigationReport(
        question="q" + SEP + WHEN.isoformat() + SEP + d1,
        generated_at=WHEN,
        document_ids=[d2],
    )
    assert injected.report_id != honest.report_id


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ((1,), ("1",)),  # int vs str
        ((["a", "b"],), ("a", "b")),  # nested list vs flattened parts
        (("",), ()),  # empty string vs nothing
        (("a", ""), ("a",)),
        (('a","b',), ("a", "b")),  # JSON-looking content is escaped
    ],
)
def test_distinct_inputs_give_distinct_ids(left: tuple, right: tuple) -> None:
    assert ids.stable_id("x", *left) != ids.stable_id("x", *right)


def test_prefix_is_bound_into_digest() -> None:
    assert ids.stable_id("a", "v").split("_", 1)[1] != ids.stable_id("b", "v").split("_", 1)[1]


@pytest.mark.parametrize("bad", [True, 1.0, None, b"x", {"k": "v"}])
def test_unsupported_id_parts_rejected(bad: object) -> None:
    with pytest.raises(TypeError):
        ids.stable_id("x", bad)  # type: ignore[arg-type]


def test_id_scheme_is_pinned() -> None:
    """Changing the canonical scheme silently would invalidate every stored ID."""
    assert ids.stable_id("ev", "doc_" + "0" * 32, 1, 0, 5, "hello") == (
        "ev_" + ids.content_digest([ids.ID_SCHEME, "ev", ["doc_" + "0" * 32, 1, 0, 5, "hello"]])[:32]
    )
    assert ids.canonical_json([ids.ID_SCHEME, "ev", ["d", 1, "é"]]) == '["regulagraph.id.v1","ev",["d",1,"é"]]'


def test_document_ids_are_format_constrained() -> None:
    """Keeps ``page_id`` (``<doc_id>:p<n>``) unambiguous."""
    with pytest.raises(ValidationError):
        ExtractedPage(document_id="doc_x:p1", page_number=2, text="")
    with pytest.raises(ValidationError):
        Evidence(document_id="anything", page_number=1, char_start=0, char_end=1, quote="a")


# 2. Report identity vs content digest ------------------------------------

def test_report_id_is_a_run_id_and_ignores_findings(report) -> None:
    changed = report.model_copy(update={"inferences": [], "relationships": []})
    changed = InvestigationReport.model_validate(changed.model_dump())
    assert changed.report_id == report.report_id
    assert changed.content_digest() != report.content_digest()


def _change_inference_statement(data: dict) -> None:
    inf = data["inferences"][0]
    new = Inference(**{**inf, "statement": "Article 2 amends circular 2018-06", "inference_id": ""})
    data["inferences"][0] = new.model_dump(mode="json")
    data["relationships"][0]["inference_id"] = new.inference_id  # keep the reference resolvable


@pytest.mark.parametrize(
    "mutate",
    [
        _change_inference_statement,
        lambda d: d["relationships"][0].update(relation_type="supersedes", relationship_id=""),
        lambda d: d["provisions"][0].update(text="different text"),
        lambda d: d.update(question="Another question?", report_id=""),
    ],
    ids=["inference_statement", "relationship_type", "provision_text", "question"],
)
def test_changed_findings_change_content_digest(report, mutate) -> None:
    data = report.model_dump(mode="json")
    mutate(data)
    changed = InvestigationReport.model_validate(data)
    assert changed.content_digest() != report.content_digest()


def test_content_digest_is_stable_across_round_trip(report) -> None:
    again = InvestigationReport.model_validate_json(report.model_dump_json())
    assert again.content_digest() == report.content_digest()
    assert len(report.content_digest()) == 64


# 3. Mandatory finalization -----------------------------------------------

def test_finalize_returns_immutable_validated_snapshot(report, doc) -> None:
    final = finalize_report(report, _sources(doc))
    assert final.report_id == report.report_id
    assert final.content_digest == report.content_digest()
    assert final.report() == report
    assert final.verified_sources == {doc.document.document_id: doc.provenance.text_digest}
    with pytest.raises(ValidationError):
        final.snapshot = "{}"  # frozen
    assert FinalizedReport.model_validate_json(final.model_dump_json()) == final


def test_finalize_snapshot_is_independent_of_later_mutation(report, doc) -> None:
    final = finalize_report(report, _sources(doc))
    report.inferences.clear()
    assert final.report().inferences  # snapshot unaffected


def test_mutation_after_construction_is_caught_by_finalize(report, doc) -> None:
    report.relationships[0].inference_id = None  # inferred relationship now unsupported; no validator ran
    with pytest.raises(ReportValidationError, match="re-validation"):
        finalize_report(report, _sources(doc))


def test_appended_unresolved_citation_is_caught(report, doc) -> None:
    report.inferences.append(Inference(statement="fabricated", model_id="m", supporting_evidence_ids=["ev_missing"]))
    with pytest.raises(ReportValidationError):
        finalize_report(report, _sources(doc))


def test_mutated_quote_is_caught(report, doc) -> None:
    ev = report.evidence[0]
    ev.quote = "X" * len(ev.quote)  # keeps length; evidence_id no longer matches
    with pytest.raises(ReportValidationError):
        finalize_report(report, _sources(doc))


def test_wrongly_typed_mutation_is_caught(report, doc) -> None:
    report.evidence[0].page_number = "two"  # type: ignore[assignment]
    with pytest.raises(ReportValidationError):
        finalize_report(report, _sources(doc))


def test_schema_valid_quote_absent_from_source_is_caught(report, doc) -> None:
    ev = Evidence(document_id=doc.document.document_id, page_number=1, char_start=0, char_end=9, quote="Article 9")
    data = report.model_dump()
    data["evidence"].append(ev.model_dump())
    with pytest.raises(EvidenceValidationError):
        finalize_report(InvestigationReport.model_validate(data), _sources(doc))


def test_mutated_source_extraction_is_caught(report, doc) -> None:
    doc.pages[1].text = doc.pages[1].text.replace("abrogee", "abrogee!")
    with pytest.raises(ReportValidationError, match="source extraction"):
        finalize_report(report, _sources(doc))


def test_missing_or_misregistered_source_is_caught(report, doc, make_pdf) -> None:
    with pytest.raises(ReportValidationError, match="not provided"):
        finalize_report(report, {})
    other = extract_pdf_bytes(make_pdf(["other"]), filename="o.pdf")
    with pytest.raises(ReportValidationError, match="wrong document_id"):
        finalize_report(report, {doc.document.document_id: other})


@pytest.mark.parametrize(
    "tamper",
    [
        lambda f: {**f, "content_digest": "0" * 64},
        lambda f: {**f, "snapshot": f["snapshot"].replace("repeals circular", "amends circular")},
        lambda f: {**f, "report_id": "rep_" + "0" * 32},
        lambda f: {**f, "verified_sources": {}},
    ],
    ids=["digest", "snapshot", "report_id", "verified_sources"],
)
def test_tampered_finalized_report_is_rejected(report, doc, tamper) -> None:
    final = finalize_report(report, _sources(doc)).model_dump()
    with pytest.raises(ValidationError):
        FinalizedReport.model_validate(tamper(final))


def test_non_canonical_snapshot_with_matching_digest_is_rejected(report, doc) -> None:
    final = finalize_report(report, _sources(doc))
    pretty = report.model_dump_json(indent=2)
    with pytest.raises(ValidationError, match="canonical"):
        FinalizedReport(
            report_id=final.report_id,
            content_digest=ids.sha256_hex(pretty.encode()),
            snapshot=pretty,
            verified_sources=final.verified_sources,
        )


# 4. Fail-closed publication status ---------------------------------------

def test_relationships_default_to_candidate(report) -> None:
    assert all(r.status == "candidate" for r in report.relationships)


def test_inferred_relationship_can_never_be_confirmed() -> None:
    with pytest.raises(ValidationError, match="only extracted"):
        RegulatoryRelationship(
            source_provision_id="p",
            target_provision_id="q",
            relation_type="repeals",
            basis="inferred",
            inference_id="inf_1",
            evidence_ids=["ev_1"],
            status="confirmed",
        )


def _extracted_report(report: InvestigationReport, status: str) -> InvestigationReport:
    data = report.model_dump()
    data["relationships"] = [
        {**data["relationships"][0], "basis": "extracted", "inference_id": None, "status": status, "relationship_id": ""}
    ]
    return InvestigationReport.model_validate(data)


def test_extracted_candidate_is_publishable(report, doc) -> None:
    final = finalize_report(_extracted_report(report, "candidate"), _sources(doc))
    assert final.report().relationships[0].status == "candidate"


def test_confirmed_extracted_relationship_is_blocked(report, doc) -> None:
    with pytest.raises(PublicationBlockedError, match="not implemented"):
        finalize_report(_extracted_report(report, "confirmed"), _sources(doc))


def test_status_mutated_to_confirmed_is_blocked(report, doc) -> None:
    rpt = _extracted_report(report, "candidate")
    rpt.relationships[0].status = "confirmed"
    with pytest.raises(PublicationBlockedError):
        finalize_report(rpt, _sources(doc))


def test_finalized_report_cannot_be_forged_with_confirmed_relationship(report, doc) -> None:
    confirmed = _extracted_report(report, "confirmed")
    snapshot = confirmed.canonical_snapshot()
    with pytest.raises(ValidationError, match="not implemented"):
        FinalizedReport(
            report_id=confirmed.report_id,
            content_digest=ids.sha256_hex(snapshot.encode()),
            snapshot=snapshot,
            verified_sources={doc.document.document_id: doc.provenance.text_digest},
        )


# 5. max_completion_tokens validation -------------------------------------

def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError("network must not be reached")


@pytest.mark.parametrize("value", [0, -1, -1024, True, 10.0, "100"])
def test_invalid_max_completion_tokens_rejected_before_request(settings_factory, value) -> None:
    adapter = MetaModelAdapter(
        settings_factory(meta_api_key="k", meta_inference_enabled=True), transport=httpx.MockTransport(_never_called)
    )
    with pytest.raises(InvalidModelRequestError, match="positive integer"):
        adapter.complete([ChatMessage(role="user", content="x")], max_completion_tokens=value)


def test_empty_messages_is_structured_error(settings_factory) -> None:
    adapter = MetaModelAdapter(
        settings_factory(meta_api_key="k", meta_inference_enabled=True), transport=httpx.MockTransport(_never_called)
    )
    with pytest.raises(InvalidModelRequestError):
        adapter.complete([])


# 6. Extraction provenance ------------------------------------------------

def test_extraction_records_provenance(doc) -> None:
    p = doc.provenance
    assert (p.extractor_name, p.extractor_version, p.extraction_mode) == ("pypdf", pypdf.__version__, "plain")
    assert p.text_digest == page_text_digest(doc.document.sha256, doc.pages)


def test_reextraction_reproduces_text_digest(make_pdf, doc) -> None:
    again = extract_pdf_bytes(make_pdf(PAGES), filename="renamed.pdf")
    assert again.provenance == doc.provenance
    different = extract_pdf_bytes(make_pdf([PAGES[0], PAGES[1] + "!"]), filename="c.pdf")
    assert different.provenance.text_digest != doc.provenance.text_digest


def test_tampered_extraction_artifact_is_rejected(doc) -> None:
    data = doc.model_dump()
    data["pages"][0]["text"] = "tampered"
    with pytest.raises(ValidationError, match="text_digest"):
        ExtractedDocument.model_validate(data)


def test_text_digest_does_not_authenticate_text_against_pdf(make_pdf, doc) -> None:
    """Known limit: a recomputed digest over altered text validates. Only re-extraction detects it."""
    data = doc.model_dump()
    data["pages"][0]["text"] = "forged"
    pages = [ExtractedPage(**p) for p in data["pages"]]
    data["provenance"]["text_digest"] = page_text_digest(doc.document.sha256, pages)
    forged = ExtractedDocument.model_validate(data)  # accepted: the digest is self-consistent

    reextracted = extract_pdf_bytes(make_pdf(PAGES), filename="c.pdf")
    assert reextracted.provenance.text_digest != forged.provenance.text_digest
