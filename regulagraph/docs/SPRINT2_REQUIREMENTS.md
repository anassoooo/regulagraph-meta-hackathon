# Sprint 2 requirements carried over from the PR #3 review

Status: **documented, not implemented.** Sprint 1 code does not meet these. They are
listed here so Sprint 2 starts from the known gaps. They were raised in PM review of PR #3.

---

## R1. Provision identity from unambiguous source locations

### Problem in Sprint 1

`provision_id = stable_id("prov", document_id, label, page_start)`. The label is a printed
string such as `"Article 5"`, and the page is only where the provision starts. That is not
unambiguous:

- **Repeated labels in one document.** The same label can appear more than once. Examples:
  "Article 1" in the main body and again in an annex. An amending circular that quotes the
  replacement text (`« Article 5 : … »`) inside its own Article 2. Two articles on one page
  that share a label because of an editing error.
  Two provisions with the same label on the same start page currently **collide** and get
  the same ID.
- **Label variants.** `Article 5`, `Art. 5`, `Article 5 bis` and `Article premier` are
  separate strings. Identity should not depend on how the label is written.
- **Multi-page provisions.** `page_start` and `page_end` record a range but not where the
  text starts and ends inside those pages. The provision text can't be traced back to exact
  source characters, and nothing can be checked against its boundaries (see R2).

### Requirements

1. **Identity is the source span, not the label.** A provision is identified by
   `document_id` plus an ordered list of **page segments**
   `(page_number, char_start, char_end)`, using the same offset rules as `Evidence`.
   `provision_id` is derived from `document_id` and the segment list. `label` stays as a
   display attribute and is not part of the identity.
2. **Multi-page provisions** have one segment per page. Segments must:
   - be in page order on consecutive pages;
   - lie inside each page's text;
   - run to the end of the page on every page except the last, and start at offset 0 on
     every page except the first, unless headers or footers are explicitly excluded (see 5).
3. **Text is derived from the source, with no normalisation.** The provision's text is the
   concatenation of `page.text[char_start:char_end]` across its segments. Any separator
   between pages must be explicit and documented, never silently added or removed. A
   verifier (like `verify_evidence`) re-derives the text from the extracted document and
   rejects any mismatch.
4. **Repeated labels are allowed and must not collide.** Two provisions with the same label
   are different provisions if their spans differ. Provisions in one document must not
   overlap, unless they are explicitly nested (for example, a quoted provision inside an
   amending article). Nesting must be recorded, not inferred from the label.
5. **Headers, footers and page numbers** repeated on every page must not be silently
   included in or dropped from the text. Either the segments exclude them using recorded
   offsets, or the text keeps them. The choice is documented and tested.
6. **Quoted provisions** (replacement text inside an amending article) are marked as
   quoted, so a relationship never treats quoted text as a provision of the amending
   document.

### Acceptance tests (minimum)

- The same label twice on one page gives two distinct IDs.
- The same label in the body and in an annex gives distinct IDs.
- A provision spanning 2 and 3 pages: its text equals the concatenated source spans
  exactly, and its ID is stable when the document is extracted again.
- `Article 5 bis` and `Article premier` are handled without label normalisation affecting
  identity.
- Tampered segments (shifted offsets, wrong page, gaps between pages) are rejected by the
  verifier.
- A quoted `Article 5` inside an amending `Article 2` is nested and marked as quoted,
  and does not collide with the amended document's own Article 5.

---

## R2. Relationship evidence must belong to the referenced provisions

### Problem in Sprint 1

`RegulatoryRelationship.evidence_ids` only has to resolve to `Evidence` somewhere in the
report. Evidence from an unrelated provision, or even an unrelated document in the same
report, is accepted as support for "Article X repeals Article Y". Verifying quotes
(`verify_evidence`) only proves the text exists. It does not tie the text to X or Y.

### Requirements

1. **Location association (structural).** Every evidence item cited by a relationship must
   lie entirely **within the span of its source provision or its target provision**. That
   means the same document, and offsets contained in one of the provision's page segments
   (this depends on R1). Anything else is rejected.
2. **Source-side evidence.** At least one cited evidence item must lie within the
   **source** provision, the one making the statement (for example, the repealing article).
3. **`basis="extracted"` relationships** also need the source-side evidence to contain an
   explicit reference to the target. For example, the target's label or official reference
   appears in the quote. This is a **lexical** check, and the rule used (patterns, matching)
   must be recorded on the relationship.
4. **`basis="inferred"` relationships** need the same location association, plus the
   cited `Inference`'s `supporting_evidence_ids` must be a subset of the relationship's
   `evidence_ids`.
5. **Enforcement point.** Add this as a report-level check with access to provision spans,
   alongside `verify_report_evidence`. Rejections raise `EvidenceValidationError` with the
   relationship and evidence IDs.

### Acceptance tests (minimum)

- Evidence inside the source provision is accepted.
- Evidence from a third provision in the same document is rejected.
- Evidence from a different document in the same report is rejected.
- Evidence that straddles a provision boundary is rejected.
- An `extracted` relationship whose quote never mentions the target is rejected.
- An inferred relationship whose inference cites evidence missing from the relationship
  is rejected.

### Explicit non-claim

R2 checks **where** evidence comes from, not **what it means**. Passing R2 does not show
that the passage semantically supports the relationship. Semantic support checking is
**not implemented** and is not part of R2. If it is added later, for example using a model
as a judge, its verdict is model output and must be stored as an `Inference`, never as
verification.

---

## R3. Record the extractor version

Evidence offsets index into the parser's output, which can change between pypdf versions.
`Document` (or `ExtractedDocument`) must record the extractor name and version, for
example `pypdf 6.21.2, plain`. Verification must refuse to compare evidence against text
produced by a different extractor version.

## R4. Measure extraction quality on real BCT documents

The Sprint 1 tests use synthetic PDFs. Before building on the extracted text, run pypdf on
a small set of public BCT circulars. Record spacing, line-break, reading-order and accent
problems, and confirm or revise the parser decision in `LICENSING.md` and `README.md`.
