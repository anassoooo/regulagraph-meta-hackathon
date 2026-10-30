"""Run one relationship investigation over the corpus and print the finalized report.

    # offline: replay a recorded/hand-written model answer (no API call)
    python scripts/investigate.py "Quelle circulaire la circulaire n°2026-10 abroge-t-elle ?" \
        --docs bct-2026-10-fr bct-2018-16-fr --replay examples/replay_e01_handwritten.json

    # live: one paid Muse call (check your credit balance first)
    python scripts/investigate.py "..." --docs bct-2026-10-fr bct-2018-16-fr --live --confirm-paid-call

``--show-prompt`` prints the retrieved provisions and stops before any model call.
The outcome (model call record, rejections, finalized snapshot and digest) is
written to eval/results/.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.redaction import configured_secrets, install_log_redaction, redact  # noqa: E402
from app.services.evaluation import load_corpus_view  # noqa: E402
from app.services.investigator import build_messages, investigate, retrieve  # noqa: E402
from app.services.meta_client import MetaModelAdapter, ReplayAdapter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--docs", nargs="+", required=True, help="manifest keys to investigate")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", type=Path, help="JSON file with the model answer to replay")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--show-prompt", action="store_true")
    parser.add_argument("--confirm-paid-call", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    install_log_redaction(settings)
    view = load_corpus_view(settings)
    sources = {view.doc_id(k): view.docs[k] for k in args.docs}
    provisions = {view.doc_id(k): view.provisions[k] for k in args.docs}

    if args.show_prompt:
        chosen = retrieve(args.question, [p for ps in provisions.values() for p in ps], sources,
                          max_chars=settings.meta_max_prompt_chars)
        messages = build_messages(args.question, chosen, sources)
        print(messages[1].content)
        print(f"\n[{sum(len(m.content) for m in messages)} prompt characters; no request sent]")
        return 0
    if args.live:
        if not args.confirm_paid_call:
            print("refusing: pass --confirm-paid-call after checking your Meta credit balance", file=sys.stderr)
            return 2
        adapter = MetaModelAdapter(settings)
    else:
        recorded = json.loads(args.replay.read_text(encoding="utf-8"))
        adapter = ReplayAdapter.from_text(json.dumps(recorded["response"], ensure_ascii=False),
                                          model_id=recorded.get("model_id", "replay"))

    out = investigate(args.question, sources, provisions, adapter, settings)
    report = out.finalized.report()
    provs = {p.provision_id: p for p in report.provisions}
    print(f"model call: {out.model_call.outcome} (model={out.model_call.model_id}, "
          f"usage={out.model_call.usage.model_dump() if out.model_call.usage else None}, "
          f"latency_ms={out.model_call.latency_ms})")
    print(f"retrieved {len(out.retrieved_provision_ids)} provisions, {out.prompt_chars} prompt chars; "
          f"{out.proposals_received} proposals, {len(out.rejected)} rejected")
    for rej in out.rejected:
        print(f"  rejected #{rej.index}: {rej.reason} - {rej.detail}")
    evidence = {e.evidence_id: e for e in report.evidence}
    for rel in report.relationships:
        src = provs[rel.source_provision_id]
        tgt = provs[rel.target_provision_id].label if rel.target_provision_id else (
            view.docs[view.key_of[rel.target_document_id]].document.reference)
        print(f"\n[{rel.status.upper()}] {view.key_of[src.document_id]} {src.label} --{rel.relation_type.value}--> {tgt}")
        print(f"  basis={rel.basis}; lexical mention of target: {'yes' if rel.lexical_match else 'no'} "
              "(neither is proof of the relationship)")
        for ev_id in rel.evidence_ids:
            ev = evidence[ev_id]
            print(f"  evidence p{ev.page_number} [{ev.char_start}:{ev.char_end}]: {ev.quote!r}")
    print(f"\ncontent_digest: {out.finalized.content_digest}")

    results = ROOT / "eval" / "results"
    results.mkdir(parents=True, exist_ok=True)
    path = results / f"investigation_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(redact(out.model_dump_json(indent=2), configured_secrets(settings)), encoding="utf-8")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
