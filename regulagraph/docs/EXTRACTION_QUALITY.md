# Extraction quality on real BCT circulars (Sprint 2, R4)

These measurements come from pypdf 6.21.2 in `plain` mode on the five PDFs listed in `data/manifest.json`, retrieved on 2026-10-30. They are reproducible:

```
python scripts/fetch_corpus.py
pytest tests/test_real_corpus.py
```

The automatic signals come from `app/services/quality.py`. The manual notes come from reading the extracted text next to the French version of the same circular. **No PDF was compared against a rendered image of its pages**, so where the extracted text looks wrong this document does not say whether the PDF or the extractor is at fault.

## Summary

| Document | Pages | Text layer | Automatic flags | Manual verdict |
|---|---|---|---|---|
| 2026-10 (fr) | 46 | every page | none | Usable. Spurious spaces inside some words. |
| 2018-16 (fr) | 18 | every page | none | Usable. The title number reads `2018 -61`. |
| 2025-01 (fr) | 7 | every page | none | Usable. |
| 2026-10 (ar) | 35 | every page | none | Readable, but spacing is unreliable. Not segmented. |
| 2018-16 (ar) | 20 | every page | none | **Unusable**: digits and letters are substituted. |

No page is empty, and there are no U+FFFD characters or Arabic presentation forms. **The automatic signals passed on the one document that is clearly broken** (2018-16 ar), and `tests/test_ingestion.py::test_quality_cannot_detect_plausible_but_wrong_text` keeps this limit visible. Page-level signals catch missing text, not wrong text.

## French

- **Word splitting.** Spurious spaces appear inside words: `se conform er`, `t e ls que`, `d e l'équité` (2026-10), and `êtr e` (the BCT site terms). Spaces also appear before hyphens in references, as in `n°2016 -35` and `n°2017 -6`.
  - Text is stored exactly as extracted, so evidence quotes keep these artefacts.
  - Lexical reference matching allows whitespace around the hyphen (`2018 -16` matches `2018-16`).
- **Reversed number in a title.** The first page of 2018-16 reads `N° 2018 -61`. The BCT index and Article 52 of 2026-10 both call this document 2018-16. Investigation uses the manifest reference, never the extracted title.
- **Tables.** Tables come out as run-together lines: the reporting tables at the end of 2026-10 (pages 44-46) and the template in 2025-01 Annex 2. They stay inside their annex provision, and no evidence in the evaluation set uses them.
- **Running headers and page numbers** are part of the page text, so provision spans keep them (R1.5). Many provisions therefore have a second segment that holds only the next page's number before the next heading. In 2026-10, 40 of the 80 provisions have more than one segment, and only some of them really continue across pages.

## Provision segmentation (`app/services/provisions.py`)

| Document | Provisions | Articles | Annexes | Titles | Nested | Repeated article numbers |
|---|---|---|---|---|---|---|
| 2026-10 | 80 | 63 | 7 | 9 | 13 | 1-11 (Annex 5 numbers its own articles) |
| 2018-16 | 49 | 40 | 2 | 6 | 0 | none |
| 2025-01 | 6 | 3 | 2 | 0 | 0 | none |

Every provision passes `verify_provision_set`: each one's text equals its source spans, and overlapping provisions are nested. Provision IDs are unique even where article labels repeat.

**Limits:**

- **Heading layout.** Headings are recognised only at the start of a line and with a separator, as in `Article 5 :`. Headings laid out in some other way would merge into the previous provision.
- **Annex detection.** An annex heading is recognised only as the first line of a page. In 2025-01 and 2024-01, the replacement text for another circular's annex (`Annexe III (nouvelle) à la circulaire n°91-24`) sits inside this circular's Annex 1 and is not marked as quoted text.
- **Quotations.** Guillemet tracking finds no quoted headings in this corpus. Unbalanced guillemets in a document would mark later headings as quoted.

## Arabic

- **2026-10 (ar).** Base Arabic letters, in logical order. Spaces between words and numbers are often missing (`لسنة2017`, `في31`) or inserted inside phrases.
- **2018-16 (ar).** The digits are systematically substituted. The French version cites `n°2006-19 du 28 novembre 2006`, and the Arabic extraction reads `عدد 09 لسنة4003 … في 48 نوفمبو 4003` (2→4, 1→0, 6→3). Letters are also substituted or displaced (`نوفمبو` for `نوفمبر`, `لموعلى ا نشور` for `وعلى المنشور`), words are merged (`المتعلقبقواعد`), and bullets come out as U+F0B7. This points to a broken font-to-Unicode mapping. The text is unusable for evidence, and OCR or another extractor would be needed.

Because of this, Sprint 2 segments and investigates the **French versions only**. The Arabic documents are in the manifest with `role: "quality_only"`.
