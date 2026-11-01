# Git history remediation: proposal (not executed)

Status: **proposal for PM decision.** No history has been rewritten, no branch deleted and no tag changed.

## What is in history

From 2026-10-30 to 2026-11-01, the recorded live runs held the full extracted text of every provision shown to the model (`results[].report.provisions[].text`):
- **Circular 2026-10:** 21 provisions, 34,102 characters, about 47% of its extracted text;
- **Circular 2018-16:** two articles, 3,069 characters.

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
- `main`;
- `feat/sprint-2-investigation`, `feat/sprint-3-demo`, `feat/sprint-3.5-live-hitl`, `feat/sprint-4-ui-polish` and `feat/sprint-5-judge-access`.

`feat/sprint-1-foundation` does not contain them. There are no tags.

**Exposure (as of 2026-11-01):**
- the repository is public, with 0 forks and 0 stars;
- GitHub also keeps the head commit of every pull request (`refs/pull/N/head`), and PRs #4 to #9 include affected commits;
- users cannot delete those refs.

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

## Recommendation

1. **Get the PM's decision now.** The cost of B or C rises once judges clone the repository: currently 0 forks.
2. **If the text must go:** B followed by a GitHub Support request is the most complete. It keeps one repository and the author-dated timeline. Schedule it before submission, and re-run Sol's review on the rewritten SHAs.
3. **If the PM accepts the copyright position:** A needs no further action, and the correction is already documented in the READMEs and the manifest.

**Verification after B or C:**
- re-run the blob scan (`tests/test_source_text_policy.py` scans the current tree; the history scan needs `git rev-list --objects --all`);
- re-run the full test suite and `scripts/demo.py --check`;
- rebuild the snapshot and compare its data files with the golden hashes.
