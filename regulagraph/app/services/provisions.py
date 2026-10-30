"""Provision spans: segmentation of extracted French BCT circulars, and verification.

Segmentation is a deterministic, line-based heuristic tuned on the layout of
BCT circulars (see docs/EXTRACTION_QUALITY.md). Boundaries are:

* article headings at the start of a line: ``Article 5 :``, ``Article premier -``,
  ``Article 1er:``, ``Article 5 bis :`` (a separator after the number is required,
  so a wrapped sentence starting "article 14 de la loi" is not a heading);
* annex headings: a line starting with ``Annexe``/``ANNEXE`` that is the first
  line of a page other than the page number;
* structural headings at the start of a line: ``Titre ...``, ``Chapitre ...``.

Text before the first boundary is the ``preamble``. Every provision runs from
its heading to the next boundary, so spans keep the source exactly, including
running headers and page numbers that fall inside them (this is deliberate:
nothing is silently dropped). Articles that follow an annex heading are that
annex's children (annexes often number their own articles from 1 again).
A heading that appears inside an open « … » quotation (an amending article
quoting replacement text) is marked ``quoted`` and nested under the provision
containing the quotation; it does not end it. A provision starts at its label,
so leading spaces or an opening guillemet stay with the preceding provision.

Arabic documents are not segmented: their extracted text is not reliable
enough to locate headings (docs/EXTRACTION_QUALITY.md).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.errors import EvidenceValidationError
from app.models.schemas import (
    PROVISION_SEGMENT_SEPARATOR,
    Evidence,
    ExtractedDocument,
    Provision,
    ProvisionSegment,
)
from app.services.evidence import evidence_from_span

# Horizontal whitespace as pypdf emits it (spaces, tabs, no-break spaces); never a newline.
_H = r"[^\S\r\n]"
_ARTICLE = re.compile(
    rf"^{_H}*(?:[«“\"]{_H}*)?(?P<label>(?:Article|ARTICLE){_H}+(?P<num>(?i:premier)|1er|\d+)"
    rf"(?:{_H}*(?P<suffix>bis|ter|quater|quinquies))?){_H}*(?:[:\-–.])",
    re.MULTILINE,
)
_STRUCTURE = re.compile(
    rf"^{_H}*(?P<label>(?:Titre|TITRE|Chapitre|CHAPITRE){_H}+[^\r\n]*?){_H}*$", re.MULTILINE
)
_ANNEX = re.compile(rf"{_H}*(?P<label>(?:Annexe|ANNEXE)\b[^\r\n]*?){_H}*$", re.MULTILINE)
_ANNEX_NUMBER = re.compile(r"(?i)^annexe\s*(?:n°\s*)?(?P<num>\d+(?:\s*bis)?|[IVXL]+)\b")
_PAGE_NUMBER_LINE = re.compile(rf"^{_H}*\d{{0,4}}{_H}*$")

PREAMBLE_LABEL = "(préambule)"


@dataclass(frozen=True, order=True)
class _Pos:
    page: int  # 1-based
    offset: int


@dataclass(frozen=True)
class _Heading:
    pos: _Pos
    kind: str  # "article" | "annex" | "heading"
    label: str
    number: str | None
    quoted: bool


def segment_provisions(extracted: ExtractedDocument) -> list[Provision]:
    """Split a French circular into provisions. Deterministic for a given extraction."""
    headings = _find_headings(extracted)
    pages = extracted.pages
    doc_id = extracted.document.document_id
    end = _Pos(len(pages), len(pages[-1].text))

    provisions: list[Provision] = []
    first = headings[0].pos if headings else end
    preamble = _build(extracted, "preamble", PREAMBLE_LABEL, None, _Pos(1, 0), first, False, None)
    if preamble is not None:
        provisions.append(preamble)

    top_level = [h for h in headings if not h.quoted]
    current_annex: Provision | None = None
    current_open: Provision | None = None  # provision a quoted heading nests under
    for i, h in enumerate(headings):
        if h.quoted:
            nxt = _next_boundary(headings, i, end)
            prov = _build(
                extracted, h.kind, h.label, h.number, h.pos, nxt, True,
                current_open.provision_id if current_open else None,
            )
            if prov is not None:
                provisions.append(prov)
            continue
        if h.kind == "annex":
            stop = next((x.pos for x in top_level if x.pos > h.pos and x.kind == "annex"), end)
        else:
            stop = next((x.pos for x in top_level if x.pos > h.pos), end)
        parent = current_annex.provision_id if current_annex is not None and h.kind != "annex" else None
        prov = _build(extracted, h.kind, h.label, h.number, h.pos, stop, False, parent)
        if prov is None:
            continue
        provisions.append(prov)
        if h.kind == "annex":
            current_annex = prov
        current_open = prov
    assert all(p.document_id == doc_id for p in provisions)
    return provisions


def _next_boundary(headings: list[_Heading], i: int, end: _Pos) -> _Pos:
    return headings[i + 1].pos if i + 1 < len(headings) else end


def _find_headings(extracted: ExtractedDocument) -> list[_Heading]:
    found: list[tuple[_Pos, str, str, str | None]] = []
    for page in extracted.pages:
        text = page.text
        for m in _ARTICLE.finditer(text):
            found.append((_Pos(page.page_number, m.start("label")), "article", m.group("label"), _article_number(m)))
        for m in _STRUCTURE.finditer(text):
            found.append((_Pos(page.page_number, m.start("label")), "heading", m.group("label"), None))
        first = _first_content_line(text)
        if first is not None:
            m = _ANNEX.match(text, first)
            if m:
                num = _ANNEX_NUMBER.match(m.group("label"))
                found.append(
                    (_Pos(page.page_number, m.start("label")), "annex", m.group("label"), num.group("num") if num else None)
                )
    found.sort(key=lambda f: f[0])
    depths = _quote_depths(extracted, [f[0] for f in found])
    return [_Heading(pos, kind, label, num, depth > 0) for (pos, kind, label, num), depth in zip(found, depths)]


def _article_number(m: re.Match[str]) -> str:
    num = m.group("num")
    num = "1" if num.lower() in {"premier", "1er"} else num
    return f"{num} {m.group('suffix')}" if m.group("suffix") else num


def _first_content_line(text: str) -> int | None:
    """Offset of the first line that is neither blank nor only a page number."""
    offset = 0
    for line in text.splitlines(keepends=True):
        if not _PAGE_NUMBER_LINE.match(line.rstrip("\r\n")):
            return offset
        offset += len(line)
    return None


def _quote_depths(extracted: ExtractedDocument, positions: list[_Pos]) -> list[int]:
    """Guillemet nesting depth (« minus », floored at 0) at each sorted position."""
    depths: list[int] = []
    depth = 0
    cursor = _Pos(1, 0)
    pages = extracted.pages
    for pos in positions:
        for chunk in _slices(pages, cursor, pos):
            for ch in chunk:
                if ch == "«":
                    depth += 1
                elif ch == "»":
                    depth = max(0, depth - 1)
        depths.append(depth)
        cursor = pos
    return depths


def _slices(pages, start: _Pos, stop: _Pos) -> Iterable[str]:
    for n in range(start.page, stop.page + 1):
        text = pages[n - 1].text
        lo = start.offset if n == start.page else 0
        hi = stop.offset if n == stop.page else len(text)
        yield text[lo:hi]


def _build(
    extracted: ExtractedDocument,
    kind: str,
    label: str,
    number: str | None,
    start: _Pos,
    stop: _Pos,
    quoted: bool,
    parent: str | None,
) -> Provision | None:
    segments: list[ProvisionSegment] = []
    for n in range(start.page, stop.page + 1):
        text = extracted.pages[n - 1].text
        lo = start.offset if n == start.page else 0
        hi = stop.offset if n == stop.page else len(text)
        if hi <= lo:
            if n == stop.page and segments:
                break  # the next provision starts at the top of this page
            # An empty page inside a span cannot be represented as a segment;
            # the provision ends before it (its remainder is left unassigned).
            break
        segments.append(ProvisionSegment(page_number=n, char_start=lo, char_end=hi))
    if not segments:
        return None
    text = PROVISION_SEGMENT_SEPARATOR.join(
        extracted.pages[s.page_number - 1].text[s.char_start : s.char_end] for s in segments
    )
    if not text.strip():
        return None
    return Provision(
        document_id=extracted.document.document_id,
        kind=kind,  # type: ignore[arg-type]
        label=label,
        number=number,
        segments=segments,
        text=text,
        quoted=quoted,
        parent_provision_id=parent,
    )


# --- verification ---------------------------------------------------------


def verify_provision(provision: Provision, extracted: ExtractedDocument) -> None:
    """Raise unless ``provision`` is an exact, contiguous span of the extracted text."""
    details = {"provision_id": provision.provision_id}
    try:
        Provision.model_validate(provision.model_dump())
    except ValidationError as exc:
        raise EvidenceValidationError("provision fails schema validation", details=details) from exc
    if provision.document_id != extracted.document.document_id:
        raise EvidenceValidationError("provision cites a different document", details=details)
    segs = provision.segments
    for i, seg in enumerate(segs):
        if seg.page_number > len(extracted.pages):
            raise EvidenceValidationError("provision cites a page that does not exist", details=details)
        page_len = len(extracted.pages[seg.page_number - 1].text)
        if seg.char_end > page_len:
            raise EvidenceValidationError("provision segment is outside the page text", details=details)
        if i < len(segs) - 1 and seg.char_end != page_len:
            raise EvidenceValidationError("provision segment must run to the end of its page", details=details)
        if i > 0 and seg.char_start != 0:
            raise EvidenceValidationError("provision continuation must start at the top of its page", details=details)
    derived = PROVISION_SEGMENT_SEPARATOR.join(
        extracted.pages[s.page_number - 1].text[s.char_start : s.char_end] for s in segs
    )
    if derived != provision.text:
        raise EvidenceValidationError("provision text does not match source spans", details=details)


def verify_provision_set(provisions: list[Provision], sources: dict[str, ExtractedDocument]) -> None:
    """Verify each provision, then the overlap rule: overlapping provisions must be nested."""
    by_id = {p.provision_id: p for p in provisions}
    for prov in provisions:
        source = sources.get(prov.document_id)
        if source is None:
            raise EvidenceValidationError(
                "source document not available for provision verification",
                details={"provision_id": prov.provision_id, "document_id": prov.document_id},
            )
        verify_provision(prov, source)
    for prov in provisions:
        parent = by_id.get(prov.parent_provision_id) if prov.parent_provision_id else None
        if parent is not None and not _contains(_interval(parent), _interval(prov)):
            raise EvidenceValidationError(
                "nested provision is not contained in its parent", details={"provision_id": prov.provision_id}
            )
    ordered = sorted(provisions, key=lambda p: (p.document_id, _interval(p)))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.document_id != a.document_id or _interval(b)[0] >= _interval(a)[1]:
                break
            if not (_is_ancestor(a, b, by_id) or _is_ancestor(b, a, by_id)):
                raise EvidenceValidationError(
                    "provisions overlap without being nested",
                    details={"provision_ids": [a.provision_id, b.provision_id]},
                )


def _interval(p: Provision) -> tuple[_Pos, _Pos]:
    first, last = p.segments[0], p.segments[-1]
    return _Pos(first.page_number, first.char_start), _Pos(last.page_number, last.char_end)


def _contains(outer: tuple[_Pos, _Pos], inner: tuple[_Pos, _Pos]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _is_ancestor(a: Provision, b: Provision, by_id: dict[str, Provision]) -> bool:
    seen: set[str] = set()
    cur = b
    while cur.parent_provision_id and cur.parent_provision_id not in seen:
        seen.add(cur.parent_provision_id)
        if cur.parent_provision_id == a.provision_id:
            return True
        nxt = by_id.get(cur.parent_provision_id)
        if nxt is None:
            return False
        cur = nxt
    return False


def evidence_within(evidence: Evidence, provision: Provision) -> bool:
    """True if the evidence span lies entirely inside one segment of the provision."""
    if evidence.document_id != provision.document_id:
        return False
    return any(
        s.page_number == evidence.page_number and s.char_start <= evidence.char_start and evidence.char_end <= s.char_end
        for s in provision.segments
    )


def locate_in_provision(provision: Provision, quote: str, extracted: ExtractedDocument) -> Evidence | None:
    """Evidence for the first verbatim occurrence of ``quote`` inside one segment of ``provision``."""
    if not quote.strip():
        return None
    for seg in provision.segments:
        page = extracted.pages[seg.page_number - 1]
        idx = page.text.find(quote, seg.char_start, seg.char_end)
        if idx >= 0:
            return evidence_from_span(page, idx, idx + len(quote))
    return None
