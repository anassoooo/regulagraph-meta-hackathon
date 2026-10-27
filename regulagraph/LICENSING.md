# Licensing notes: PyMuPDF (AGPL-3.0)

> This is an engineering risk note, not legal advice. Get a qualified review before
> any public deployment, and check the hackathon's own licence rules.

## Facts

- RegulaGraph depends on **PyMuPDF 1.x** (`pymupdf>=1.24,<2`). The installed 1.28.2 declares:
  *"Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License"*.
- We have no Artifex commercial licence, so the **AGPL-3.0** applies.
- The repository has **no LICENSE file**. That means RegulaGraph itself has no stated licence.
- Other runtime dependencies (checked from installed package metadata) are MIT or
  BSD-3-Clause, with two exceptions. `typing-extensions` is PSF-2.0. `certifi`, pulled in
  by httpx, is MPL-2.0: weak, file-level copyleft that only matters if we modify certifi's
  own files.

## Why a public GitHub repository is not enough on its own

AGPL obligations depend on how the software is conveyed and run. Whether the source is
visible does not decide them. A public repo does **not** automatically satisfy any of these:

1. **Licence compatibility of the combined work.** RegulaGraph imports PyMuPDF in the same
   process. The FSF reads that as one combined work. Anyone who conveys or network-serves
   the combined work must do so under terms compatible with the AGPL-3.0. A repo with no
   licence grants no such terms, so the combined work has no compliant licence.
2. **Section 13 (network use).** If a modified version runs as a network service (for
   example, a hosted demo), its users must be *offered* the Corresponding Source **of the
   version actually running**. A link to a repo is only enough if it exactly matches the
   deployed code, including uncommitted changes, configuration-dependent code and any
   patched dependency.
3. **Corresponding Source scope.** This covers all of the work's source and the scripts
   needed to build and run it. Secrets and data are not included, but the code that
   loads them is.
4. **Notices.** Licence and copyright notices for PyMuPDF and MuPDF must be preserved
   when the software is conveyed. That includes bundling them in a container image.
5. **Third-party constraints.** Hackathon terms, or the employer and internship policies
   of anyone contributing, may restrict releasing code under the AGPL. Check this
   separately.

## Current exposure

- **Local development and running tests:** low. AGPL obligations are mainly triggered by
  conveying the software or making it available over a network.
- **Public demo deployment:** Section 13 applies. This is the main risk.
- **Submitting a public repository:** item 1 applies as soon as we choose a licence, or
  fail to choose one.

## Options (a decision for the team, not made here)

| Option | Effect |
|---|---|
| A. License RegulaGraph under AGPL-3.0 | Simplest compliant path. Add `LICENSE` and a source-offer link in the deployed UI/API. Downstream users inherit copyleft. |
| B. Swap PyMuPDF for a permissive parser | `pypdf` (BSD-3), `pdfminer.six` / `pdfplumber` (MIT), or `pypdfium2` (Apache-2.0 / BSD-3). Text extraction is isolated in `app/services/document_parser.py`, so the swap is cheap now. Extraction quality on BCT PDFs needs benchmarking. |
| C. Artifex commercial licence | Not compatible with the $0 budget. |

Recommendation: decide before the first public deployment. If the team prefers a
permissive licence such as MIT or Apache-2.0, choose option B during Sprint 2, before
more code depends on PyMuPDF-specific behaviour.
