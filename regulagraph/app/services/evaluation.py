"""Evaluation of relationship investigation against manually written cases (eval/cases.json).

Two kinds of measurement, never mixed:

* ``score_case``: how the *model + pipeline* did on one case, from an actual
  ``InvestigationOutcome`` (live run). Checks retrieval, relation type,
  direction, target and whether the cited evidence covers the gold quote.
* ``structural_probes``: how the *validators* treat hand-made proposals (the
  gold answer and deliberate perturbations) fed through the same pipeline via
  a replayed response. This needs no model and measures only what validation
  can and cannot reject; it is not a model benchmark.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.models.schemas import Evidence, ExtractedDocument, InvestigationReport, Provision, RelationType
from app.services.corpus import Corpus
from app.services.investigator import InvestigationOutcome, investigate
from app.services.meta_client import ReplayAdapter
from app.services.provisions import locate_in_provision

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "eval" / "cases.json"


class ProvisionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: str
    kind: str = "article"
    number: str
    in_annex: bool = False


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["document", "provision"]
    doc: str
    kind_t: str | None = None
    number: str | None = None
    in_annex: bool = False


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ProvisionSpec
    target: TargetSpec
    acceptable_types: list[RelationType] = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)


class Forbidden(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_doc: str
    target_doc: str
    source_number: str | None = None
    target_number: str | None = None
    types: list[RelationType] | None = None


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    phenomenon: str
    polarity: Literal["positive", "negative"]
    documents: list[str] = Field(min_length=1)
    question: str
    expected: Expected | None = None
    forbidden: list[Forbidden] = Field(default_factory=list)
    notes: str = ""

    def model_post_init(self, _: Any) -> None:
        if (self.polarity == "positive") != (self.expected is not None):
            raise ValueError(f"{self.id}: positive cases need 'expected', negative cases must not have it")
        if self.polarity == "negative" and not self.forbidden:
            raise ValueError(f"{self.id}: negative cases need 'forbidden'")


class EvalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    review: dict[str, Any]
    relation_semantics: str
    cases: list[EvalCase]


def load_cases(path: Path = DEFAULT_CASES) -> EvalSet:
    data = EvalSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if len({c.id for c in data.cases}) != len(data.cases):
        raise ValueError("case ids must be unique")
    return data


# --- resolution ---------------------------------------------------------------

class CorpusView:
    """Extracted documents and provisions keyed by manifest key."""

    def __init__(self, corpus: Corpus, docs: Mapping[str, ExtractedDocument],
                 provisions: Mapping[str, Sequence[Provision]]) -> None:
        self.corpus = corpus
        self.docs = dict(docs)  # key -> extraction
        self.provisions = {k: list(v) for k, v in provisions.items()}  # key -> provisions
        self.key_of = {d.document.document_id: k for k, d in self.docs.items()}

    def doc_id(self, key: str) -> str:
        return self.docs[key].document.document_id

    def find(self, doc: str, kind: str, number: str, in_annex: bool) -> Provision:
        found = [
            p for p in self.provisions[doc]
            if p.kind == kind and p.number == number and (p.parent_provision_id is not None) == in_annex
        ]
        if len(found) != 1:
            raise LookupError(f"{doc} {kind} {number} in_annex={in_annex}: {len(found)} matches")
        return found[0]

    def scope(self, case: EvalCase) -> tuple[dict[str, ExtractedDocument], dict[str, list[Provision]]]:
        sources = {self.doc_id(k): self.docs[k] for k in case.documents}
        return sources, {self.doc_id(k): self.provisions[k] for k in case.documents}


def resolve_expected(case: EvalCase, view: CorpusView) -> tuple[Provision, str, Evidence]:
    """(source provision, target id, gold evidence located inside the source)."""
    exp = case.expected
    assert exp is not None
    source = view.find(exp.source.doc, exp.source.kind, exp.source.number, exp.source.in_annex)
    if exp.target.kind == "document":
        target_id = view.doc_id(exp.target.doc)
    else:
        target_id = view.find(exp.target.doc, exp.target.kind_t or "article", exp.target.number or "",
                              exp.target.in_annex).provision_id
    gold = locate_in_provision(source, exp.evidence_quote, view.docs[exp.source.doc])
    if gold is None:
        raise LookupError(f"{case.id}: gold quote not found in the source provision")
    return source, target_id, gold


# --- scoring a real outcome -----------------------------------------------------

class CaseResult(BaseModel):
    case_id: str
    phenomenon: str
    polarity: str
    model_outcome: str
    retrieved_provision_ids: list[str] = Field(default_factory=list)
    retrieved_source: bool | None = None
    retrieved_target: bool | None = None
    published: list[dict[str, Any]]
    rejected: int
    correct: bool
    errors: list[str]
    citation_ok: bool | None = None


def _summaries(report: InvestigationReport, view: CorpusView) -> list[dict[str, Any]]:
    provs = {p.provision_id: p for p in report.provisions}
    out = []
    for r in report.relationships:
        src = provs[r.source_provision_id]
        tgt = provs.get(r.target_provision_id) if r.target_provision_id else None
        out.append({
            "relationship_id": r.relationship_id,
            "source": f"{view.key_of.get(src.document_id)} {src.label}",
            "relation_type": r.relation_type.value,
            "target": (f"{view.key_of.get(tgt.document_id)} {tgt.label}" if tgt
                       else f"{view.key_of.get(r.target_document_id or '')} (document)"),
            "status": r.status,
            "lexical_match": r.lexical_match is not None,
        })
    return out


def _overlaps(a: Evidence, b: Evidence) -> bool:
    return (a.document_id == b.document_id and a.page_number == b.page_number
            and a.char_start < b.char_end and b.char_start < a.char_end)


def score_case(case: EvalCase, outcome: InvestigationOutcome, view: CorpusView) -> CaseResult:
    report = outcome.finalized.report()
    provs = {p.provision_id: p for p in report.provisions}
    evidence = {e.evidence_id: e for e in report.evidence}
    errors: list[str] = []
    citation_ok: bool | None = None
    retrieved_source = retrieved_target = None
    correct = False

    if case.polarity == "positive":
        source, target_id, gold = resolve_expected(case, view)
        exp = case.expected
        assert exp is not None
        retrieved_source = source.provision_id in outcome.retrieved_provision_ids
        retrieved_target = exp.target.kind == "document" or target_id in outcome.retrieved_provision_ids
        matched = False
        for r in report.relationships:
            same_source = r.source_provision_id == source.provision_id
            if same_source and r.target_id == target_id:
                matched = True
                if r.relation_type in exp.acceptable_types:
                    correct = True
                    citation_ok = any(_overlaps(evidence[e], gold) for e in r.evidence_ids)
                    if not citation_ok:
                        errors.append("citation_miss")
                else:
                    errors.append(f"type_error:{r.relation_type.value}")
            elif r.source_provision_id == target_id and r.target_id == source.provision_id:
                errors.append("direction_error")
            elif same_source and r.relation_type in exp.acceptable_types:
                errors.append("target_error")
        if not matched and not errors:
            errors.append("missing")
        if correct and citation_ok is False:
            correct = False
    else:
        shown = set(outcome.retrieved_provision_ids)
        sources_needed = [
            view.find(f.source_doc, "article", f.source_number, False).provision_id
            for f in case.forbidden if f.source_number
        ]
        retrieved_source = all(p in shown for p in sources_needed) if sources_needed else None
        targets_needed = [
            view.find(f.target_doc, "article", f.target_number, False).provision_id
            for f in case.forbidden if f.target_number
        ]
        retrieved_target = all(p in shown for p in targets_needed) if targets_needed else None
        for r in report.relationships:
            src = provs[r.source_provision_id]
            tgt = provs.get(r.target_provision_id) if r.target_provision_id else None
            tgt_doc = tgt.document_id if tgt else r.target_document_id
            for f in case.forbidden:
                if (view.key_of.get(src.document_id) == f.source_doc and view.key_of.get(tgt_doc or "") == f.target_doc
                        and (f.source_number is None or src.number == f.source_number)
                        and (f.target_number is None or (tgt is not None and tgt.number == f.target_number))
                        and (f.types is None or r.relation_type in f.types)):
                    errors.append(f"forbidden_published:{r.relation_type.value}")
        correct = not errors
    if outcome.model_call.outcome != "ok":
        errors.append(f"model_outcome:{outcome.model_call.outcome}")
        if case.polarity == "positive":
            correct = False
    return CaseResult(
        case_id=case.id, phenomenon=case.phenomenon, polarity=case.polarity,
        model_outcome=outcome.model_call.outcome, retrieved_provision_ids=outcome.retrieved_provision_ids,
        retrieved_source=retrieved_source,
        retrieved_target=retrieved_target, published=_summaries(report, view),
        rejected=len(outcome.rejected), correct=correct, errors=errors, citation_ok=citation_ok,
    )


# --- retrieval recall (no model) --------------------------------------------------

class RetrievalResult(BaseModel):
    case_id: str
    source_retrieved: bool
    target_retrieved: bool | None  # None: the target is a whole document
    retrieved: int
    prompt_chars: int


def retrieval_recall(case: EvalCase, view: CorpusView, settings: Settings) -> RetrievalResult:
    from app.services.investigator import build_messages, retrieve

    source, target_id, _ = resolve_expected(case, view)
    sources, provisions = view.scope(case)
    chosen = retrieve(case.question, [p for ps in provisions.values() for p in ps], sources,
                      max_chars=settings.meta_max_prompt_chars)
    ids_ = {p.provision_id for p in chosen}
    is_doc = case.expected is not None and case.expected.target.kind == "document"
    return RetrievalResult(
        case_id=case.id,
        source_retrieved=source.provision_id in ids_,
        target_retrieved=None if is_doc else target_id in ids_,
        retrieved=len(chosen),
        prompt_chars=sum(len(m.content) for m in build_messages(case.question, chosen, sources)),
    )


# --- structural probes (no model) -----------------------------------------------

class ProbeResult(BaseModel):
    case_id: str
    probe: str
    expectation: Literal["accept", "reject", "undetectable"]
    published: bool
    rejection: str | None
    as_expected: bool


def _proposal(source: Provision, rtype: str, target_kind: str, target_id: str,
              quotes: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "source_provision_id": source.provision_id, "relation_type": rtype, "target_kind": target_kind,
        "target_id": target_id, "quotes": [{"provision_id": p, "text": t} for p, t in quotes],
        "rationale": "structural probe (hand-made proposal, not model output)",
    }


def _run_probe(case: EvalCase, view: CorpusView, settings: Settings, proposal: dict[str, Any],
               name: str, expectation: Literal["accept", "reject", "undetectable"],
               shown: Sequence[Provision]) -> ProbeResult:
    sources, provisions = view.scope(case)
    adapter = ReplayAdapter.from_text(json.dumps({"relationships": [proposal]}, ensure_ascii=False),
                                      model_id="structural-probe")
    # Retrieval is bypassed: probes test validation, not retrieval.
    out = investigate(case.question, sources, provisions, adapter, settings, selected=shown)
    published = bool(out.finalized.report().relationships)
    rejection = out.rejected[0].reason if out.rejected else None
    ok = published if expectation in {"accept", "undetectable"} else not published
    return ProbeResult(case_id=case.id, probe=name, expectation=expectation, published=published,
                       rejection=rejection, as_expected=ok)


def structural_probes(case: EvalCase, view: CorpusView, settings: Settings) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    if case.polarity == "positive":
        exp = case.expected
        assert exp is not None
        source, target_id, gold = resolve_expected(case, view)
        target_prov = next((p for p in view.provisions[exp.target.doc] if p.provision_id == target_id), None)
        q = [source, *([target_prov] if target_prov else [])]
        tkind = exp.target.kind
        good_type = exp.acceptable_types[0].value
        quote = [(source.provision_id, exp.evidence_quote)]
        results.append(_run_probe(case, view, settings, _proposal(source, good_type, tkind, target_id, quote),
                                  "gold", "accept", q))
        paraphrase = "Ainsi, " + exp.evidence_quote
        results.append(_run_probe(case, view, settings,
                                  _proposal(source, good_type, tkind, target_id, [(source.provision_id, paraphrase)]),
                                  "paraphrased_quote", "reject", q))
        wrong_type = next(t.value for t in RelationType if t not in exp.acceptable_types)
        results.append(_run_probe(case, view, settings, _proposal(source, wrong_type, tkind, target_id, quote),
                                  f"wrong_type:{wrong_type}", "undetectable", q))
        results.append(_run_probe(case, view, settings,
                                  _proposal(source, good_type, "document", "doc_" + "0" * 32, quote),
                                  "fabricated_target", "reject", q))
        if target_prov is not None:
            results.append(_run_probe(case, view, settings,
                                      _proposal(target_prov, good_type, "provision", source.provision_id, quote),
                                      "reversed_direction", "reject", q))
            other = next(p for p in view.provisions[exp.source.doc]
                         if p.kind == "article" and p.provision_id not in {source.provision_id, target_id})
            other_quote = other.text.split("\n")[0][:40].strip()
            results.append(_run_probe(case, view, settings,
                                      _proposal(source, good_type, tkind, target_id,
                                                [(source.provision_id, exp.evidence_quote), (other.provision_id, other_quote)]),
                                      "quote_from_unrelated_provision", "reject", [source, target_prov, other]))
    else:
        for i, f in enumerate(case.forbidden):
            src = (view.find(f.source_doc, "article", f.source_number, False) if f.source_number
                   else next(p for p in view.provisions[f.source_doc] if p.kind == "article"))
            quote = src.text.split("\n")[0][:60].strip()
            if f.target_number:
                tgt = view.find(f.target_doc, "article", f.target_number, False)
                tkind, tid, q = "provision", tgt.provision_id, [src, tgt]
            else:
                tkind, tid, q = "document", view.doc_id(f.target_doc), [src]
            rtype = (f.types[0] if f.types else RelationType.REFERENCES).value
            results.append(_run_probe(case, view, settings,
                                      _proposal(src, rtype, tkind, tid, [(src.provision_id, quote)]),
                                      f"forbidden_{i}_with_real_quote", "undetectable", q))
    return results


def load_corpus_view(settings: Settings, corpus: Corpus | None = None) -> CorpusView:
    """Load and segment every investigation document in the manifest (PDFs must be fetched)."""
    from app.services.corpus import load_document, load_manifest
    from app.services.provisions import segment_provisions

    corpus = corpus or load_manifest()
    docs: dict[str, ExtractedDocument] = {}
    provisions: dict[str, list[Provision]] = {}
    for entry in corpus.documents:
        if entry.role != "investigation":
            continue
        docs[entry.key] = load_document(entry, settings)
        provisions[entry.key] = segment_provisions(docs[entry.key])
    return CorpusView(corpus, docs, provisions)
