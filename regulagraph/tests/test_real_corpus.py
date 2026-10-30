"""Checks against the real BCT PDFs. Skipped unless they were fetched into data/raw/.

    python scripts/fetch_corpus.py && pytest tests/test_real_corpus.py
"""

import pytest

from app.core.config import Settings
from app.services.corpus import DEFAULT_RAW_DIR, load_document, load_manifest
from app.services.provisions import segment_provisions, verify_provision_set
from app.services.quality import assess_extraction

CORPUS = load_manifest()


def _load(key: str):
    entry = CORPUS.get(key)
    if not (DEFAULT_RAW_DIR / entry.filename).is_file():
        pytest.skip(f"{entry.filename} not downloaded (run scripts/fetch_corpus.py)")
    return load_document(entry, Settings(_env_file=None))


@pytest.mark.parametrize("key", [e.key for e in CORPUS.documents])
def test_every_page_has_text_and_page_count_matches_manifest(key) -> None:
    doc = _load(key)
    assert doc.document.page_count == CORPUS.get(key).pages
    assert assess_extraction(doc).flagged_pages == {}


@pytest.mark.parametrize("key", [e.key for e in CORPUS.documents if e.language == "fr"])
def test_french_provisions_verify_against_source(key) -> None:
    doc = _load(key)
    provisions = segment_provisions(doc)
    verify_provision_set(provisions, {doc.document.document_id: doc})
    assert len({p.provision_id for p in provisions}) == len(provisions)


def test_2026_10_article_52_and_repeated_annex_labels() -> None:
    doc = _load("bct-2026-10-fr")
    provisions = segment_provisions(doc)
    art52 = [p for p in provisions if p.label == "Article 52"]
    assert len(art52) == 1
    assert "abroge et remplace la circulaire n°2018-16" in art52[0].text
    article_2 = [p for p in provisions if p.kind == "article" and p.number == "2"]
    assert len(article_2) == 2  # the body's Article 2 and Annex 5's own Article 2
    body, annex = sorted(article_2, key=lambda p: p.page_start)
    assert body.parent_provision_id is None and annex.parent_provision_id is not None
    assert not any(p.quoted for p in provisions)
