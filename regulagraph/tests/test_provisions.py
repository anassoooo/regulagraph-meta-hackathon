"""R1: provision identity from source spans (docs/SPRINT2_REQUIREMENTS.md)."""

import pytest

from app.core.errors import EvidenceValidationError
from app.models.schemas import PROVISION_SEGMENT_SEPARATOR, Provision, ProvisionSegment
from app.services.document_parser import extract_pdf_bytes
from app.services.evidence import locate_quote
from app.services.provisions import (
    evidence_within,
    locate_in_provision,
    segment_provisions,
    verify_provision,
    verify_provision_set,
)


@pytest.fixture
def extract(make_pdf):
    def _extract(pages: list[str]):
        return extract_pdf_bytes(make_pdf(pages), filename="c.pdf")

    return _extract


def _by_label(provisions: list[Provision], label: str) -> list[Provision]:
    return [p for p in provisions if p.label == label]


def _verify_all(doc, provisions) -> None:
    verify_provision_set(provisions, {doc.document.document_id: doc})


def test_preamble_articles_and_text_are_exact_source_spans(extract) -> None:
    doc = extract(["CIRCULAIRE N 2026-99\nVu la loi;\nArticle premier : Objet.\nArticle 2 : Entree en vigueur."])
    provs = segment_provisions(doc)
    assert [(p.kind, p.label, p.number) for p in provs] == [
        ("preamble", "(préambule)", None),
        ("article", "Article premier", "1"),
        ("article", "Article 2", "2"),
    ]
    text = doc.pages[0].text
    for p in provs:
        seg = p.segments[0]
        assert p.text == text[seg.char_start : seg.char_end]
    assert "".join(p.text for p in provs) == text  # contiguous, nothing dropped or added
    _verify_all(doc, provs)


def test_same_label_twice_on_one_page_gets_two_ids(extract) -> None:
    doc = extract(["Article 3 : Premier texte.\nArticle 3 : Texte repete par erreur."])
    provs = _by_label(segment_provisions(doc), "Article 3")
    assert len(provs) == 2
    assert provs[0].provision_id != provs[1].provision_id
    _verify_all(doc, segment_provisions(doc))


def test_same_label_in_body_and_annex_gets_distinct_ids_and_annex_nesting(extract) -> None:
    doc = extract([
        "Article 1 : Corps.\nArticle 2 : Fin du corps.",
        "ANNEXE 1\nCahier des charges\nArticle 1 : Article de l annexe.\nArticle 2 : Suite.",
    ])
    provs = segment_provisions(doc)
    annex = next(p for p in provs if p.kind == "annex")
    assert (annex.label, annex.number) == ("ANNEXE 1", "1")
    body_1, annex_1 = _by_label(provs, "Article 1")
    assert body_1.provision_id != annex_1.provision_id
    assert body_1.parent_provision_id is None
    assert annex_1.parent_provision_id == annex.provision_id
    _verify_all(doc, provs)  # overlap of annex and its articles is allowed: it is recorded nesting


@pytest.mark.parametrize("page_count", [2, 3])
def test_multi_page_provision_text_and_stable_id(extract, page_count) -> None:
    pages = ["Article 1 : Debut du texte"] + [f"suite page {i}" for i in range(2, page_count + 1)]
    pages[-1] += "\nArticle 2 : Autre."
    doc = extract(pages)
    art1 = _by_label(segment_provisions(doc), "Article 1")[0]
    assert [s.page_number for s in art1.segments] == list(range(1, page_count + 1))
    expected = PROVISION_SEGMENT_SEPARATOR.join(
        doc.pages[s.page_number - 1].text[s.char_start : s.char_end] for s in art1.segments
    )
    assert art1.text == expected
    again = _by_label(segment_provisions(extract(pages)), "Article 1")[0]
    assert again.provision_id == art1.provision_id  # re-extraction gives the same identity
    verify_provision(art1, doc)


def test_bis_and_premier_are_labels_not_identity(extract) -> None:
    doc = extract(["Article premier : A.\nArticle 5 : B.\nArticle 5 bis : C.\nArticle 1er : D."])
    provs = [p for p in segment_provisions(doc) if p.kind == "article"]
    assert [(p.label, p.number) for p in provs] == [
        ("Article premier", "1"),
        ("Article 5", "5"),
        ("Article 5 bis", "5 bis"),
        ("Article 1er", "1"),
    ]
    assert len({p.provision_id for p in provs}) == 4  # same number "1", different spans


def test_wrapped_sentence_starting_with_article_is_not_a_heading(extract) -> None:
    doc = extract(["Article 1 : Les conditions des\narticles 20 et 21 de la loi.\nArticle 14 de la loi s applique."])
    assert [p.label for p in segment_provisions(doc) if p.kind == "article"] == ["Article 1"]


def test_quoted_article_is_nested_and_marked_quoted(extract) -> None:
    doc = extract([
        "Article 1 : Objet.\nArticle 2 : L article 5 de la circulaire 2018-16 est remplace :\n"
        "« Article 5 : Nouveau texte de l article. »\nArticle 3 : Entree en vigueur.",
    ])
    provs = segment_provisions(doc)
    art2 = _by_label(provs, "Article 2")[0]
    quoted = _by_label(provs, "Article 5")[0]
    assert quoted.quoted and quoted.parent_provision_id == art2.provision_id
    assert "Article 5 : Nouveau" in art2.text  # the quotation did not end Article 2
    assert not _by_label(provs, "Article 3")[0].quoted  # quote closed before Article 3
    _verify_all(doc, provs)


def test_titles_are_boundaries(extract) -> None:
    doc = extract(["Article 1 : Texte.\nTitre II : Des agents\nArticle 2 : Texte."])
    provs = segment_provisions(doc)
    assert [p.kind for p in provs] == ["article", "heading", "article"]
    assert "Titre" not in _by_label(provs, "Article 1")[0].text


# Tampering ----------------------------------------------------------------

def _tampered(p: Provision, segments: list[tuple[int, int, int]], doc) -> Provision:
    segs = [ProvisionSegment(page_number=n, char_start=s, char_end=e) for n, s, e in segments]
    text = PROVISION_SEGMENT_SEPARATOR.join(doc.pages[n - 1].text[s:e] for n, s, e in segments)
    return Provision(**{**p.model_dump(exclude={"provision_id", "segments", "text"}), "segments": segs, "text": text})


@pytest.fixture
def two_page(extract):
    doc = extract(["Article 1 : Debut du texte", "suite\nArticle 2 : Autre."])
    return doc, _by_label(segment_provisions(doc), "Article 1")[0]


def test_shifted_offsets_change_identity_and_text_must_still_match(two_page) -> None:
    doc, art1 = two_page
    seg0, seg1 = art1.segments
    shifted = Provision(
        **{**art1.model_dump(exclude={"provision_id"}),
           "segments": [{**seg0.model_dump(), "char_start": 1}, seg1.model_dump()],
           "text": art1.text[:-2] + "!"}
    )  # schema-valid: lengths agree, but the text is not what the source says
    with pytest.raises(EvidenceValidationError, match="does not match source"):
        verify_provision(shifted, doc)


def test_segment_must_run_to_end_of_page_before_continuing(two_page) -> None:
    doc, art1 = two_page
    seg0, seg1 = art1.segments
    gap = _tampered(art1, [(1, seg0.char_start, seg0.char_end - 1), (2, 0, seg1.char_end)], doc)
    with pytest.raises(EvidenceValidationError, match="end of its page"):
        verify_provision(gap, doc)


def test_continuation_must_start_at_top_of_page(two_page) -> None:
    doc, art1 = two_page
    seg0, seg1 = art1.segments
    late = _tampered(art1, [(1, seg0.char_start, seg0.char_end), (2, 1, seg1.char_end)], doc)
    with pytest.raises(EvidenceValidationError, match="top of its page"):
        verify_provision(late, doc)


def test_wrong_page_and_page_beyond_document_are_rejected(two_page) -> None:
    doc, art1 = two_page
    moved = _tampered(art1, [(2, 0, 5)], doc)
    moved = Provision(**{**moved.model_dump(exclude={"provision_id"}), "text": doc.pages[0].text[0:5]})
    with pytest.raises(EvidenceValidationError, match="does not match source"):
        verify_provision(moved, doc)
    beyond = Provision(**{**art1.model_dump(exclude={"provision_id", "segments", "text"}),
                          "segments": [{"page_number": 9, "char_start": 0, "char_end": 1}], "text": "x"})
    with pytest.raises(EvidenceValidationError, match="does not exist"):
        verify_provision(beyond, doc)


def test_overlapping_provisions_must_be_nested(extract) -> None:
    doc = extract(["Article 1 : Un texte assez long.\nArticle 2 : Autre."])
    art1 = _by_label(segment_provisions(doc), "Article 1")[0]
    overlapping = _tampered(art1, [(1, 4, 20)], doc)
    with pytest.raises(EvidenceValidationError, match="overlap"):
        _verify_all(doc, [art1, overlapping])
    nested = Provision(**{**overlapping.model_dump(exclude={"provision_id"}), "parent_provision_id": art1.provision_id})
    _verify_all(doc, [art1, nested])


def test_mutated_provision_text_is_caught(two_page) -> None:
    doc, art1 = two_page
    art1.text = "X" * len(art1.text)  # no validator runs on assignment
    with pytest.raises(EvidenceValidationError):
        verify_provision(art1, doc)


# Locating evidence inside a provision -------------------------------------

def test_locate_in_provision_only_searches_its_own_span(extract) -> None:
    doc = extract(["Article 1 : abrogee.\nArticle 2 : abrogee aussi."])
    art1, art2 = [p for p in segment_provisions(doc) if p.kind == "article"]
    ev = locate_in_provision(art2, "abrogee", doc)
    assert ev is not None and evidence_within(ev, art2) and not evidence_within(ev, art1)
    assert locate_in_provision(art1, "aussi", doc) is None
    first = locate_quote(doc.pages[0], "abrogee")  # page-level search finds Article 1's occurrence
    assert evidence_within(first, art1)
