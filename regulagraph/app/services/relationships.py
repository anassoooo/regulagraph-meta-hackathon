"""Structural and lexical checks tying relationship evidence to provisions (R2).

What is checked, at finalization:

* every cited evidence item lies inside one segment of the source provision or
  of the target provision (location association);
* at least one cited item lies inside the source provision;
* a quoted provision (text of another instrument) is never a source;
* ``lexical_match``, when present, points into source-side evidence and the
  matched text is the target's designation under rule ``regulagraph.lexical.v1``;
* ``basis="extracted"`` relationships carry such a match (schema-enforced);
* an inferred relationship's inference cites only the relationship's evidence.

What is NOT checked: whether the passage means what the relationship says.
A lexical match proves the target is named in the source provision, nothing
more ("abroge la circulaire 2018-16" and "sans préjudice de la circulaire
2018-16" both match). Semantic support is not implemented.
"""

import re
from collections.abc import Mapping

from app.core.errors import EvidenceValidationError
from app.models.schemas import (
    Evidence,
    ExtractedDocument,
    InvestigationReport,
    LexicalMatch,
    Provision,
    RegulatoryRelationship,
)
from app.services.provisions import evidence_within

_REFERENCE_NUMBER = re.compile(r"(\d{2,4})\s*-\s*(\d+)")


def document_reference_pattern(reference: str | None) -> re.Pattern[str] | None:
    """'Circulaire n°2018-16' -> matches '2018-16', '2018 -16', '2018-016'; not '2018-160'."""
    m = _REFERENCE_NUMBER.search(reference or "")
    if m is None:
        return None
    year, num = m.group(1), int(m.group(2))
    return re.compile(rf"(?<!\d){year}\s*-\s*0*{num}(?!\d)")


def provision_reference_pattern(provision: Provision) -> re.Pattern[str] | None:
    """'Article 5' -> 'article 5', 'articles 5'; 'Article premier' -> also '1er', 'premier'."""
    if provision.number is None:
        return None
    number, _, suffix = provision.number.partition(" ")
    alternatives = [re.escape(number)] + (["premier", "1er"] if number == "1" else [])
    num = "(?:" + "|".join(alternatives) + ")" + (rf"\s*{re.escape(suffix)}" if suffix else "")
    if provision.kind == "article":
        return re.compile(rf"(?i)\barticles?\s+{num}(?![\w])")
    if provision.kind == "annex":
        return re.compile(rf"(?i)\bannexe\s*(?:n°\s*)?{num}(?![\w])")
    return None


def target_pattern(
    rel: RegulatoryRelationship,
    provisions: Mapping[str, Provision],
    sources: Mapping[str, ExtractedDocument],
) -> re.Pattern[str] | None:
    if rel.target_provision_id is not None:
        return provision_reference_pattern(provisions[rel.target_provision_id])
    source = sources.get(rel.target_document_id or "")
    return document_reference_pattern(source.document.reference if source else None)


def find_lexical_match(evidence: Evidence, pattern: re.Pattern[str] | None) -> LexicalMatch | None:
    if pattern is None:
        return None
    m = pattern.search(evidence.quote)
    if m is None:
        return None
    return LexicalMatch(evidence_id=evidence.evidence_id, match_start=m.start(), match_end=m.end())


def verify_relationship_support(
    report: InvestigationReport, sources: Mapping[str, ExtractedDocument]
) -> None:
    provisions = {p.provision_id: p for p in report.provisions}
    evidence = {e.evidence_id: e for e in report.evidence}
    inferences = {i.inference_id: i for i in report.inferences}
    for rel in report.relationships:
        details = {"relationship_id": rel.relationship_id}
        source = provisions[rel.source_provision_id]
        target = provisions.get(rel.target_provision_id) if rel.target_provision_id else None
        if source.quoted:
            raise EvidenceValidationError("a quoted provision cannot be a relationship source", details=details)
        if rel.target_document_id is not None and rel.target_document_id == source.document_id:
            raise EvidenceValidationError("a provision cannot relate to its own document", details=details)
        cited = [evidence[i] for i in rel.evidence_ids]
        for ev in cited:
            if not (evidence_within(ev, source) or (target is not None and evidence_within(ev, target))):
                raise EvidenceValidationError(
                    "evidence lies outside the source and target provisions",
                    details={**details, "evidence_id": ev.evidence_id},
                )
        if not any(evidence_within(ev, source) for ev in cited):
            raise EvidenceValidationError("no cited evidence lies inside the source provision", details=details)

        if rel.lexical_match is not None:
            _verify_lexical_match(rel, rel.lexical_match, evidence, source, provisions, sources, details)

        if rel.basis == "inferred":
            inference = inferences[rel.inference_id]  # type: ignore[index]
            extra = set(inference.supporting_evidence_ids) - set(rel.evidence_ids)
            if extra:
                raise EvidenceValidationError(
                    "the inference cites evidence the relationship does not",
                    details={**details, "evidence_ids": sorted(extra)},
                )


def _verify_lexical_match(
    rel: RegulatoryRelationship,
    match: LexicalMatch,
    evidence: Mapping[str, Evidence],
    source: Provision,
    provisions: Mapping[str, Provision],
    sources: Mapping[str, ExtractedDocument],
    details: dict,
) -> None:
    ev = evidence[match.evidence_id]
    if not evidence_within(ev, source):
        raise EvidenceValidationError("lexical_match must cite source-side evidence", details=details)
    pattern = target_pattern(rel, provisions, sources)
    if pattern is None:
        raise EvidenceValidationError("the target has no designation to match lexically", details=details)
    if not (match.match_end <= len(ev.quote) and pattern.fullmatch(ev.quote, match.match_start, match.match_end)):
        raise EvidenceValidationError("lexical_match does not name the target", details=details)
