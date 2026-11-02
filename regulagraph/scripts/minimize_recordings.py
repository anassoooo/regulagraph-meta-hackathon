"""Remove full BCT provision text from recorded live runs (Sprint 5 compliance remediation). No model call.

    python scripts/minimize_recordings.py            # rewrite eval/results/live_*.json in place
    python scripts/minimize_recordings.py --check    # exit 1 if any recording still holds provision text

Each recorded run stored its finalized report, including the full extracted text of every provision shown to
the model (``report.provisions[].text``). That text is not needed:

- replay (app/services/replay.py) reads only the provision IDs from a recording and re-derives the text from
  the hash-verified PDFs;
- rescoring (scripts/run_eval.py rescore) rebuilds each provision from the corpus by its content-derived ID,
  and fails if an ID is not in the corpus.

Only that one field is removed. Everything else is kept byte for byte, including ``model_call.raw_text``
(the recorded model output that replay feeds back through the pipeline), the evidence quotations, provision
IDs, labels, segments (page and character offsets), the content digest, usage and scores. A top-level
``source_text_removed`` note records what was removed and why. Files keep their JSON layout (indent 2,
UTF-8, no trailing newline), so the git diff shows only the removed lines and the note.

The stored report no longer hashes to ``content_digest``; the digest is still checked on every replay, which
recomputes it from the PDFs.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
NOTE = {
    "field": "results[].report.provisions[].text",
    "on": "2026-11-01",
    "reason": ("Full BCT provision text is not redistributed. Replay re-derives it from the hash-verified official "
               "PDFs by provision ID; rescoring rebuilds it from the corpus. Nothing else in this file was changed."),
}


def minimize(data: dict) -> int:
    """Remove provision text in place; return how many provisions had text."""
    removed = 0
    for record in data.get("results", []):
        for provision in (record.get("report") or {}).get("provisions", []):
            if "text" in provision:
                del provision["text"]
                removed += 1
    if removed or "source_text_removed" not in data:
        data["source_text_removed"] = NOTE
    return removed


def has_provision_text(data: dict) -> bool:
    return any("text" in p for r in data.get("results", []) for p in (r.get("report") or {}).get("provisions", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report; exit 1 if provision text remains")
    args = parser.parse_args()
    files = sorted(RESULTS.glob("live_*.json"))
    dirty = []
    for path in files:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        data = json.loads(text)
        if args.check:
            if has_provision_text(data):
                dirty.append(path.name)
            continue
        if json.dumps(data, indent=2, ensure_ascii=False) != text:
            print(f"{path.name}: unexpected JSON layout; not rewritten", file=sys.stderr)
            return 1
        removed = minimize(data)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"{path.name}: removed text from {removed} provision(s)")
    if args.check:
        print("provision text found in: " + ", ".join(dirty) if dirty else f"ok: {len(files)} recordings, no provision text")
        return 1 if dirty else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
