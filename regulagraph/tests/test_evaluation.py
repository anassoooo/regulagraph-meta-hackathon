"""Evaluation dataset contract and scoring logic."""

import json
from datetime import UTC, datetime

import pytest

from app.core import ids
from app.core.config import Settings
from app.services.corpus import DEFAULT_RAW_DIR, CorpusEntry, attach_metadata, load_manifest
from app.services.document_parser import extract_pdf_bytes
from app.services.evaluation import (
    CorpusView,
    EvalCase,
    load_cases,
    load_corpus_view,
    resolve_expected,
    score_case,
    structural_probes,
)
from app.services.investigator import investigate
from app.services.meta_client import ReplayAdapter
from app.services.provisions import segment_provisions

WHEN = datetime(2026, 10, 30, tzinfo=UTC)


# Dataset contract ------------------------------------------------------------

def test_cases_file_meets_the_sprint_requirements() -> None:
    data = load_cases()
    cases = data.cases
    assert len(cases) >= 10
    assert {c.polarity for c in cases} == {"positive", "negative"}
    phenomena = {c.phenomenon for c in cases}
    assert {"negation", "direction", "relation_type"} <= phenomena
    assert any("historical" in p for p in phenomena)
    keys = {e.key for e in load_manifest().documents if e.role == "investigation"}
    assert all(set(c.documents) <= keys for c in cases)
    assert data.review["reviewed_by"] is None  # not reviewed by a domain expert yet; do not claim otherwise


def test_case_contract_is_enforced() -> None:
    with pytest.raises(ValueError):
        EvalCase(id="x", phenomenon="p", polarity="positive", documents=["d"], question="q")
    with pytest.raises(ValueError):
        EvalCase(id="x", phenomenon="p", polarity="negative", documents=["d"], question="q")


# Scoring on a synthetic corpus ----------------------------------------------

NEW = ["Article 1 : Objet.\nArticle 2 : La presente circulaire abroge la circulaire n 2018-16.\n"
       "Article 3 : En application de l article 1 de la presente circulaire, les banques declarent."]
OLD = ["Article 1 : Ancien objet."]


def _entry(key: str, ref: str, data: bytes) -> CorpusEntry:
    return CorpusEntry(key=key, reference=ref, title=ref, publication_date="2026-01-01", language="fr",
                       role="investigation", source_url="https://example.org/x.pdf", filename=f"{key}.pdf",
                       sha256=ids.sha256_hex(data), bytes=len(data), retrieved_on="2026-10-30", pages=1)


@pytest.fixture
def view(make_pdf):
    new_pdf, old_pdf = make_pdf(NEW), make_pdf(OLD)
    docs = {
        "new": attach_metadata(extract_pdf_bytes(new_pdf, filename="n.pdf"), _entry("new", "Circulaire n°2026-99", new_pdf)),
        "old": attach_metadata(extract_pdf_bytes(old_pdf, filename="o.pdf"), _entry("old", "Circulaire n°2018-16", old_pdf)),
    }
    return CorpusView(load_manifest(), docs, {k: segment_provisions(d) for k, d in docs.items()})


POSITIVE = {
    "id": "T1", "phenomenon": "explicit_repeal", "polarity": "positive", "documents": ["new", "old"],
    "question": "Quelle circulaire la circulaire 2026-99 abroge-t-elle ?",
    "expected": {"source": {"doc": "new", "kind": "article", "number": "2"},
                 "target": {"kind": "document", "doc": "old"},
                 "acceptable_types": ["repeals", "supersedes"],
                 "evidence_quote": "abroge la circulaire n 2018-16"},
}
IMPLEMENTS = {
    "id": "T2", "phenomenon": "implementation_direction", "polarity": "positive", "documents": ["new"],
    "question": "En application de quel article l article 3 est-il pris ?",
    "expected": {"source": {"doc": "new", "kind": "article", "number": "3"},
                 "target": {"kind": "provision", "doc": "new", "kind_t": "article", "number": "1"},
                 "acceptable_types": ["implements", "references"],
                 "evidence_quote": "En application de l article 1"},
}
NEGATIVE = {
    "id": "T3", "phenomenon": "direction", "polarity": "negative", "documents": ["new", "old"],
    "question": "La circulaire 2018-16 abroge-t-elle la circulaire 2026-99 ?",
    "forbidden": [{"source_doc": "old", "target_doc": "new"}],
}


def _outcome(view, case, proposals, **replay):
    sources, provisions = view.scope(case)
    text = replay.pop("text", json.dumps({"relationships": proposals}, ensure_ascii=False))
    adapter = ReplayAdapter.from_text(text, **replay)
    shown = [p for ps in provisions.values() for p in ps if p.kind != "heading"]
    return investigate(case.question, sources, provisions, adapter, Settings(_env_file=None), selected=shown,
                       clock=lambda: WHEN)


def _p(view, key, number, rtype, target_kind, target, quote_key, quote_number, quote):
    src = view.find(key, "article", number, False)
    qp = view.find(quote_key, "article", quote_number, False)
    return {"source_provision_id": src.provision_id, "relation_type": rtype, "target_kind": target_kind,
            "target_id": target, "quotes": [{"provision_id": qp.provision_id, "text": quote}], "rationale": "r"}


def test_correct_answer_scores_correct_with_citation(view) -> None:
    case = EvalCase(**POSITIVE)
    out = _outcome(view, case, [_p(view, "new", "2", "repeals", "document", view.doc_id("old"),
                                   "new", "2", "abroge la circulaire n 2018-16")])
    result = score_case(case, out, view)
    assert result.correct and result.citation_ok and result.errors == []


def test_wrong_type_is_a_type_error(view) -> None:
    case = EvalCase(**POSITIVE)
    out = _outcome(view, case, [_p(view, "new", "2", "amends", "document", view.doc_id("old"),
                                   "new", "2", "abroge la circulaire n 2018-16")])
    result = score_case(case, out, view)
    assert not result.correct and result.errors == ["type_error:amends"]


def test_citation_elsewhere_in_source_is_a_citation_miss(view) -> None:
    case = EvalCase(**POSITIVE)
    out = _outcome(view, case, [_p(view, "new", "2", "repeals", "document", view.doc_id("old"),
                                   "new", "2", "La presente circulaire")])
    result = score_case(case, out, view)
    assert not result.correct and result.errors == ["citation_insufficient:repeals"]


def test_reversed_direction_is_a_direction_error(view) -> None:
    case = EvalCase(**IMPLEMENTS)
    art1 = view.find("new", "article", "1", False)
    out = _outcome(view, case, [_p(view, "new", "1", "implements", "provision",
                                   view.find("new", "article", "3", False).provision_id, "new", "1", "Objet")])
    assert out.finalized.report().relationships  # structurally valid: Article 1 quoting itself
    result = score_case(case, out, view)
    assert "direction_error:implements" in result.errors and not result.correct
    assert art1.provision_id in result.published[0]["source"] or result.published[0]["source"].endswith("Article 1")


def test_no_answer_is_missing_and_refusal_is_recorded(view) -> None:
    case = EvalCase(**POSITIVE)
    assert score_case(case, _outcome(view, case, []), view).errors == ["missing"]
    refused = score_case(case, _outcome(view, case, [], text=None, refusal="no"), view)
    assert not refused.correct and "model_outcome:refused" in refused.errors


def test_negative_case_flags_forbidden_relationships(view) -> None:
    case = EvalCase(**NEGATIVE)
    assert score_case(case, _outcome(view, case, []), view).correct
    bad = _p(view, "old", "1", "repeals", "document", view.doc_id("new"), "old", "1", "Ancien objet")
    result = score_case(case, _outcome(view, case, [bad]), view)
    assert not result.correct and result.errors == ["forbidden_published:repeals"]


# Real corpus (skipped unless the PDFs were fetched) ---------------------------

def _real_view():
    if not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"):
        pytest.skip("corpus PDFs not downloaded (run scripts/fetch_corpus.py)")
    return load_corpus_view(Settings(_env_file=None))


@pytest.fixture(scope="module")
def real_view():
    return _real_view()


@pytest.mark.parametrize("case", [c for c in load_cases().cases if c.polarity == "positive"], ids=lambda c: c.id)
def test_gold_answers_resolve_to_unique_provisions_and_exact_quotes(real_view, case) -> None:
    source, target_id, gold = resolve_expected(case, real_view)
    doc = real_view.docs[case.expected.source.doc]
    assert doc.pages[gold.page_number - 1].text[gold.char_start : gold.char_end] == case.expected.evidence_quote
    assert target_id != source.provision_id


@pytest.mark.parametrize("case", load_cases().cases, ids=lambda c: c.id)
def test_structural_probes_behave_as_documented(real_view, case) -> None:
    results = structural_probes(case, real_view, Settings(_env_file=None))
    assert results and all(r.as_expected for r in results), [r.model_dump() for r in results if not r.as_expected]


def test_retrieval_recall_on_real_corpus(real_view) -> None:
    """Gold source and target provisions are retrieved for every positive case (E12 regression)."""
    from app.services.evaluation import retrieval_recall

    settings = Settings(_env_file=None)
    misses = [
        r.model_dump() for c in load_cases().cases if c.polarity == "positive"
        for r in [retrieval_recall(c, real_view, settings)]
        if not r.source_retrieved or r.target_retrieved is False
    ]
    assert misses == []


def test_named_provisions_of_negative_cases_are_retrieved(real_view) -> None:
    """E13 regression: a named target must not be crowded out by articles the source cites."""
    from app.services.investigator import retrieve

    settings = Settings(_env_file=None)
    for case in [c for c in load_cases().cases if c.polarity == "negative"]:
        sources, provisions = real_view.scope(case)
        shown = {p.provision_id for p in retrieve(case.question, [p for ps in provisions.values() for p in ps],
                                                  sources, max_chars=settings.meta_max_prompt_chars)}
        for f in case.forbidden:
            for doc, number in [(f.source_doc, f.source_number), (f.target_doc, f.target_number)]:
                if number:
                    assert real_view.find(doc, "article", number, False).provision_id in shown, (case.id, doc, number)


def test_live_runner_refuses_without_flag_or_explicit_cases(monkeypatch) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "load_corpus_view", lambda s: pytest.fail("must refuse before loading anything"))
    settings = Settings(_env_file=None)
    assert module.run_live(settings, confirmed=False, only=["E01"], reasoning_effort="low") == 2
    assert module.run_live(settings, confirmed=True, only=[], reasoning_effort="low") == 2


def test_completion_token_default_is_2048_and_bounded(settings_factory) -> None:
    assert settings_factory().meta_max_completion_tokens == 2048
    assert settings_factory(meta_max_completion_tokens=4096).meta_max_completion_tokens == 4096
    for bad in (0, -1, 32769):
        with pytest.raises(ValueError):
            settings_factory(meta_max_completion_tokens=bad)


def test_negative_case_with_target_missing_from_prompt_is_not_validated(view) -> None:
    case = EvalCase(**{**NEGATIVE, "forbidden": [{"source_doc": "old", "source_number": "1",
                                                  "target_doc": "new", "target_number": "2"}]})
    sources, provisions = view.scope(case)
    only_source = [view.find("old", "article", "1", False)]
    adapter = ReplayAdapter.from_text('{"relationships": []}')
    out = investigate(case.question, sources, provisions, adapter, Settings(_env_file=None), selected=only_source,
                      clock=lambda: WHEN)
    result = score_case(case, out, view)
    assert result.correct  # nothing forbidden was published ...
    assert result.retrieved_target is False and not result.coverage_complete
    assert not result.validated  # ... but the case tested nothing about the target


def test_live_runner_preflight_blocks_the_call_when_a_required_provision_is_missing(real_view, monkeypatch) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class NoCalls:
        def __init__(self, *a, **k):
            pass

        def complete(self, *a, **k):
            pytest.fail("the API must not be called when preflight fails")

    written = {}
    monkeypatch.setattr(module, "load_corpus_view", lambda s: real_view)
    monkeypatch.setattr(module, "retrieve", lambda *a, **k: [])  # nothing retrieved
    monkeypatch.setattr(module, "MetaModelAdapter", NoCalls)
    monkeypatch.setattr(module, "_write", lambda kind, payload: written.setdefault(kind, payload) and Path("x"))
    assert module.run_live(Settings(_env_file=None), confirmed=True, only=["E13"], reasoning_effort="low") == 0
    (result,) = written["live"]["results"]
    assert result["preflight"] == "retrieval_failure" and result["api_called"] is False
    assert written["live"]["summary"]["api_calls"] == 0


@pytest.mark.parametrize("replay", [{"finish_reason": "length"}, {"text": None, "refusal": "no"}, {"text": "not json"}])
def test_negative_case_without_a_usable_answer_is_not_a_success(view, replay) -> None:
    """E10 live run: truncated output published nothing; that must not count as a correct abstention."""
    case = EvalCase(**NEGATIVE)
    result = score_case(case, _outcome(view, case, [], **replay), view)
    assert not result.correct and not result.validated
    assert any(e.startswith("model_outcome:") for e in result.errors)


def test_rescore_of_recorded_live_results(real_view, tmp_path, monkeypatch) -> None:
    """Recorded live runs: the truncated E10 run and the first E13 run (target not shown) are not validated."""
    import importlib.util
    import shutil
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "load_corpus_view", lambda s: real_view)
    monkeypatch.setattr(module, "CURRENT_SUMMARY", tmp_path / "current_scoring_summary.json")
    results = Path(__file__).resolve().parents[1] / "eval" / "results"
    expected = {"live_20261030T151822Z.json": ("E13", True, False), "live_20261030T153423Z.json": ("E10", False, False),
                "live_20261030T153246Z.json": ("E13", True, True)}
    copies = []
    for name in expected:
        if not (results / name).is_file():
            pytest.skip("recorded live results not present")
        copies.append(Path(shutil.copy(results / name, tmp_path / name)))
    assert module.run_rescore(Settings(_env_file=None), copies) == 0
    for copy in copies:
        (record,) = json.loads(copy.read_text(encoding="utf-8"))["results"]
        case_id, correct, validated = expected[copy.name]
        assert (record["rescored"]["case_id"], record["rescored"]["correct"], record["rescored"]["validated"]) == (
            case_id, correct, validated)
        assert record["rescored"]["scoring_version"] == 2
        original = json.loads((results / copy.name).read_text(encoding="utf-8"))["results"][0]
        assert record["score"] == original["score"]  # the historical score is never rewritten
    summary = json.loads((tmp_path / "current_scoring_summary.json").read_text(encoding="utf-8"))
    assert summary["scoring_version"] == 2 and "E10" not in summary["validated_cases_latest_run"]


# Scoring v2 (independent review of 0b3f0a2) ------------------------------------

def _repeal(view, rtype="repeals", quote="abroge la circulaire n 2018-16"):
    return _p(view, "new", "2", rtype, "document", view.doc_id("old"), "new", "2", quote)


def test_correct_repeal_plus_incorrect_amendment_on_same_endpoints_is_not_correct(view) -> None:
    case = EvalCase(**POSITIVE)
    result = score_case(case, _outcome(view, case, [_repeal(view), _repeal(view, "amends")]), view)
    assert [v["verdict"] for v in result.verdicts] == ["match", "type_error"]
    assert not result.correct and not result.validated
    assert result.errors == ["type_error:amends"]


def test_multiple_acceptable_types_on_same_endpoints_are_all_matches(view) -> None:
    case = EvalCase(**POSITIVE)  # acceptable: repeals, supersedes
    result = score_case(case, _outcome(view, case, [_repeal(view), _repeal(view, "supersedes")]), view)
    assert [v["verdict"] for v in result.verdicts] == ["match", "match"]
    assert result.correct and result.validated and result.citation_ok


def test_correct_relationship_plus_reversed_one_is_not_correct(view) -> None:
    case = EvalCase(**IMPLEMENTS)
    good = _p(view, "new", "3", "implements", "provision", view.find("new", "article", "1", False).provision_id,
              "new", "3", "En application de l article 1")
    reversed_ = _p(view, "new", "1", "implements", "provision", view.find("new", "article", "3", False).provision_id,
                   "new", "1", "Objet")
    result = score_case(case, _outcome(view, case, [good, reversed_]), view)
    assert [v["verdict"] for v in result.verdicts] == ["match", "direction_error"]
    assert not result.correct


@pytest.mark.parametrize("fragment", ["ab", "abroge", "2018-16", "circulaire n 2018"])
def test_fragment_cannot_validate_a_repeal_citation(view, fragment) -> None:
    """Any-character overlap used to accept 'ab'; the cited span must contain a sufficient span."""
    case = EvalCase(**POSITIVE)
    result = score_case(case, _outcome(view, case, [_repeal(view, quote=fragment)]), view)
    assert result.verdicts[0]["verdict"] == "citation_insufficient"
    assert result.citation_ok is False and not result.correct and not result.validated


def test_annotated_sufficient_alternative_is_accepted_and_longer_span_contains_gold(view) -> None:
    case = EvalCase(**{**POSITIVE, "expected": {**POSITIVE["expected"],
                                                "evidence_quote": "La presente circulaire abroge la circulaire n 2018-16",
                                                "sufficient_quotes": ["abroge la circulaire n 2018-16"]}})
    alternative = score_case(case, _outcome(view, case, [_repeal(view)]), view)
    assert alternative.correct  # the quote equals an annotated sufficient alternative
    longer = score_case(case, _outcome(view, case, [_repeal(view, quote=(
        "Article 2 : La presente circulaire abroge la circulaire n 2018-16."))]), view)
    assert longer.correct  # contains the full annotated span
    unannotated = EvalCase(**{**POSITIVE, "expected": {**POSITIVE["expected"],
                                                       "evidence_quote": "La presente circulaire abroge la circulaire n 2018-16"}})
    assert not score_case(unannotated, _outcome(view, unannotated, [_repeal(view)]), view).correct


def test_confusable_target_is_a_target_error_but_other_targets_are_unscored(view) -> None:
    case = EvalCase(**IMPLEMENTS)
    other = _p(view, "new", "3", "references", "provision", view.find("new", "article", "2", False).provision_id,
               "new", "3", "En application de l article 1")
    good = _p(view, "new", "3", "implements", "provision", view.find("new", "article", "1", False).provision_id,
              "new", "3", "En application de l article 1")
    result = score_case(case, _outcome(view, case, [good, other]), view)
    assert [v["verdict"] for v in result.verdicts] == ["match", "unscored"]
    assert result.correct  # an unannotated extra relationship is listed, not credited or penalised
    wrong_doc = EvalCase(**POSITIVE)
    to_self_doc = _p(view, "new", "2", "repeals", "document", view.doc_id("old"), "new", "2",
                     "abroge la circulaire n 2018-16")
    assert score_case(wrong_doc, _outcome(view, wrong_doc, [to_self_doc]), view).correct


def test_real_corpus_gold_and_sufficient_spans_resolve(real_view) -> None:
    from app.services.evaluation import resolve_sufficient_spans

    for case in [c for c in load_cases().cases if c.polarity == "positive"]:
        spans = resolve_sufficient_spans(case, real_view)
        assert spans and all(s.quote for s in spans)
