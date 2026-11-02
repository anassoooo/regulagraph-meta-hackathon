# Git history remediation: decision and record

Status: **decided: Option A** (PM decision, 2026-11-02). No history has been rewritten, no branch deleted and no tag changed.

## Decision

For the hackathon submission the PM chose **Option A**:
- **Git history is preserved.** The historical recordings described below stay reachable through earlier commits, branches and pull-request refs.
- **Substantial BCT text is removed from the current public files.** The release branch contains none of the affected versions; the six files on `main` are replaced when the release pull request is merged.
- **Unresolved questions are documented, not settled.** Whether BCT circulars fall under the official-texts exemption, and whether the historical copies are permitted reuse, has not been confirmed by the BCT, a court or counsel. This decision is **not a legal clearance**. If the BCT or another rights holder raises a concern, options B and C below remain available. A private local backup of all refs was made for that case; its checksum has not yet been verified by the owner.

The analysis that led to the decision follows.

## What is in history

From 2026-10-30 to 2026-11-01, the recorded live runs held the full extracted text of every provision shown to the model (`results[].report.provisions[].text`):
- **Circular 2026-10:** 21 provisions, 34,102 characters, about 47% of its extracted text;
- **Circular 2018-16:** two articles, 3,069 characters.

A re-scan on 2026-11-02 of a fresh mirror of the public repository (all 18 refs, including GitHub's `refs/pull/*`) counted each character once and collapsed whitespace: 29,060 distinct characters of circular 2026-10 (41.4% of its extracted text) and 2,862 of circular 2018-16 (10.0%). The longest continuous passage is 7,416 characters. Nothing from circular 2025-01 or the Arabic versions was found.

Sprint 5 removed the field from the current files (`scripts/minimize_recordings.py`) and added a test that blocks its return (`tests/test_source_text_policy.py`). Earlier commits still contain it.

**Affected objects.** A scan of every blob reachable from any ref looked for more than 600 consecutive characters of corpus page text. It found **14 blobs**, all versions of these six paths:

```
regulagraph/eval/results/live_20261030T151756Z.json   (E01)
regulagraph/eval/results/live_20261030T151822Z.json   (E13, first pilot)
regulagraph/eval/results/live_20261030T153246Z.json   (E13)
regulagraph/eval/results/live_20261030T153322Z.json   (E12)
regulagraph/eval/results/live_20261030T153355Z.json   (E05)
regulagraph/eval/results/live_20261030T153423Z.json   (E10)
```

No other file and no other path in history matched. No PDF has ever been committed.

**Commits that added or changed these files** (all 2026-10-30):
- `9635203` Live Muse pilot (E01, E13), smoke-test record, targeted retrieval fixes;
- `0b3f0a2` Final Sprint 2 live pass: E13 re-run, E12, E05, E10;
- `fd9c67d` Remediate independent review of 0b3f0a2: scoring v2, citation coverage, redaction.

**Refs that contain them:**
- `main`, which still has all six files in its current tree until PR #9 or a later clean commit is merged;
- `feat/sprint-2-investigation`, `feat/sprint-3-demo`, `feat/sprint-3.5-live-hitl`, `feat/sprint-4-ui-polish` and `feat/sprint-5-judge-access`.

`feat/sprint-1-foundation` does not contain them. There are no tags. The current trees of `feat/sprint-5-judge-access`, `feat/demo-live-first`, `docs/build-window-kickoff` and `release/rc1` contain none of the affected versions.

**Exposure (as of 2026-11-01):**
- the repository is public, with 0 forks and 0 stars;
- GitHub also keeps the head commit of every pull request (`refs/pull/N/head`), and PRs #4 to #9 include affected commits;
- users cannot delete those refs.

## Rights basis (sources read 2026-11-02)

This section records primary sources. It is not legal advice and not a legal clearance.

**Tunisian Law 94-36 of 24 February 1994 on literary and artistic property, as amended by Law 2009-33 of 23 June 2009.**
- Law 2009-33, article 2, adds a last paragraph to article 1 of Law 94-36. The French text reads: "La protection au titre du droit d'auteur s'étend aux expressions et ne couvre pas : [...] - les textes officiels d'ordre législatif, administratif ou judiciaire et leurs traductions officielles."
  - Read in the French PDF of Law 2009-33 on WIPO Lex (`tn027fr_1.pdf`, record [6161](https://www.wipo.int/wipolex/fr/legislation/details/6161)).
  - The consolidated text on [legislation-securite.tn](https://legislation-securite.tn/wp-content/uploads/sites/5/2023/11/Loi-n°-94-36-du-24-Fevrier-1994-Fr.pdf) says the same, with the footnote "Article premier – Dernier paragraphe (nouveau) – Ajouté par la loi n° 2009-33 du 23 juin 2009".
  - The 1994 original text (WIPO Lex [3840](https://www.wipo.int/wipolex/fr/legislation/details/3840)) has no such exclusion: it dates from 2009.
- Law 94-36, article 11, permits quotations ("citations et emprunts") from a work lawfully made public, consistent with fair practice and justified by a scientific, educational or information purpose, with the source and the author's name.
- These copies are WIPO Lex and a consolidation site, not the Journal Officiel de la République Tunisienne itself (Law 2009-33 was published in JORT n° 52 of 30 June 2009).

**BCT site terms** (`https://www.bct.gov.tn/bct/siteprod/documents/termes.pdf`; SHA-256 `266004b5…3b77`, the same on 2026-10-30 and 2026-11-02):
- "Sous réserve des dispositions légales en vigueur, exemptant les textes réglementaires officiels du droit d'auteur, l'ensemble du contenu du site web est protégé par le droit d'auteur et constitue la propriété exclusive de la BCT."
- Downloads and printing are allowed "à des fins exclusivement personnelles". Copying all or part of the site to another site or an internal network is not allowed. Selling, distributing, or deriving commercial products requires prior BCT agreement.
- Quotations must appear exactly, with the website cited as the source. Links must keep the site's integrity, name its address, and open in a new window.

**Interpretation (not established):**
- BCT circulars are regulatory instruments issued by a national public institution, so they plausibly fall under "textes officiels d'ordre ... administratif" and under the terms' exemption for "textes réglementaires officiels".
- Neither text names BCT circulars, and no court decision, BCT statement or legal opinion confirming this has been found.
- If the exemption applies, the historical text raises little copyright concern. If it does not, the historical recordings exceed what the terms allow (copying to another site), and article 11 quotation would not obviously cover 41% of a circular.
- The current tree is designed to meet the terms either way: short exact quotations, the source cited, and links opening in a new window.

## Options

### A. Keep history as it is (current state)
- **Effect:** current files are clean, and the old text stays reachable through the commits above, the branches and the PR pages.
- **Cost:** none. All SHAs, PR links and review references stay valid.
- **Residual risk:** about 37,000 characters of BCT text stay publicly downloadable. That is acceptable only if the PM accepts the copyright position, for example that the terms' exemption for official regulatory texts applies.

### B. Rewrite history in place, then ask GitHub to purge cached views
1. Back up first: `git clone --mirror` into a private, local-only location. The backup contains the text, so it must never be pushed.
2. Run `git filter-repo` with a blob callback that applies `minimize_recordings.minimize` to each historical version of the six paths, and keep author and committer dates.
3. Check that every rewritten commit's tree is identical to the original except for those six paths.
4. Force-push `main` and the five affected branches.
5. Ask GitHub Support to remove cached views and the `refs/pull/*` heads of PRs #4–#9, following GitHub's procedure for removing sensitive data.
   GitHub's documentation (read 2026-11-02) says `refs/pull/*` are read-only and that "GitHub Support won't remove non-sensitive data, and will only assist in the removal of sensitive data in cases where we determine that the risk can't be mitigated by rotating affected credentials." Copyrighted text is not a credential, so Support may decline, leaving PRs #4–#9 with the text after the rewrite.

**Consequences:**
- **Every commit SHA from `9635203` onwards changes,** on `main` and on the five branches. Among the SHAs quoted in docs, PR descriptions and review comments:
  - `9bb7c1f` (Sprint 3.5 merge), `2226b32` (Sprint 4 merge) and `7422699` (PR #9 head) stop resolving;
  - `5beecb4` is named in the golden-test docstring as the Sprint 3 capture point. It also post-dates `9635203`, so it stops resolving too.
- **Pull requests:**
  - merged PRs #4–#8 keep pointing at the old SHAs, and their diffs remain visible until GitHub Support purges them;
  - open PR #9 must be force-updated or re-created, and Sol's review must be redone against the new SHAs.
- **Collaborators** (PM, Sol, anyone with a clone) must re-clone or hard-reset. An old clone that is pushed again would bring the text back.
- **Reproducibility:**
  - file contents other than the removed field are unchanged, so replay digests, golden API hashes and scores are unaffected;
  - the "before" hashes in `eval/README.md` no longer match any public commit;
  - `snapshot-manifest.json` commit IDs change, so the snapshot must be rebuilt.
- **Build-window evidence:** author and committer dates survive, but GitHub's push and PR timestamps for the rewritten commits will be the force-push time. The PR records (creation and merge times) remain the independent evidence.

**Effort:** about 1–2 hours, plus GitHub Support turnaround, which can take days and may not finish before the 2026-11-04 deadline.

### C. Publish the submission from a new repository
- **Steps:**
  1. Create a new public repository from a rewritten history (option B's filter, pushed to a fresh remote), or from a single squashed commit.
  2. Make the current repository private.
- **Benefits:** the old commits are no longer public (subject to GitHub's caches), and no force-push to the shared repository is needed.
- **Consequences:**
  - all existing PR and review links stop working for anyone without access;
  - a squashed history loses the commit timeline that the build-window disclosure relies on, while a rewritten history keeps author dates but not the original GitHub PR timestamps;
  - the hackathon submission must point at the new URL.

### D. Make the repository private now, decide later
- **Effect:** stops new exposure immediately and can be undone.
- **Cost:** the hackathon requires a public repository, so this is only a holding step before A, B or C.

## Recommendation (before the decision)

1. **Get the PM's decision now.** The cost of B or C rises once judges clone the repository: currently 0 forks.
2. **If the text must go:** B followed by a GitHub Support request is the most complete. It keeps one repository and the author-dated timeline. Schedule it before submission, and re-run Sol's review on the rewritten SHAs.
3. **If the PM accepts the copyright position:** A needs no further action, and the correction is already documented in the READMEs and the manifest.

**Verification after B or C:**
- re-run the blob scan (`tests/test_source_text_policy.py` scans the current tree; the history scan needs `git rev-list --objects --all`);
- re-run the full test suite and `scripts/demo.py --check`;
- rebuild the snapshot and compare its data files with the golden hashes.
