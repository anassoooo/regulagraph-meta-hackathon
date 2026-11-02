"""Export a static, replay-only snapshot of the demo for public hosting (Sprint 5). No model is called.

    python scripts/build_snapshot.py                 # writes build/snapshot/ (git-ignored)
    python scripts/build_snapshot.py --out site      # the directory render.yaml publishes

Runs on a machine that already has the hash-verified corpus PDFs (python scripts/fetch_corpus.py):

1. the demo preflight: PDFs match their SHA-256, recordings are consistent, and every scenario's replay
   reproduces its recorded Muse run exactly (scripts/demo.py);
2. the real app, in-process, with inference and live mode disabled and network access blocked, answers
   GET /api/scenarios, POST /api/scenarios/{id}/replay for each scenario, and GET /api/evaluation.
   Each response body is written byte for byte as a static JSON file;
3. the web client is copied, with a <meta name="regulagraph-snapshot"> tag (build commit and date) that
   switches it to static mode, and a static STATIC SNAPSHOT banner;
4. the output is checked: no PDF or other unexpected file, no credential from .env, no string longer
   than the API's quotation and rationale limits, and no full provision text.

Quotations are verified when the snapshot is built. The hosted page does not re-verify them.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Before any settings are read: no inference, no live mode, and no key in this process, whatever .env says.
os.environ["META_INFERENCE_ENABLED"] = "false"
os.environ["LIVE_MODE_ENABLED"] = "false"
os.environ["MODEL_API_KEY"] = ""

import httpx  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402

WEB_DIR = ROOT / "app" / "web"
WEB_FILES = ("index.html", "app.js", "view.js", "style.css", "favicon.svg")
DEFAULT_OUT = ROOT / "build" / "snapshot"
# The API caps quotations and model rationales at 600 characters (app/services/investigator.py) and
# questions at 500. Anything much longer in an exported file would be source text that must not be there.
MAX_STRING_CHARS = 700
MIN_PROVISION_CHARS_CHECKED = 200
SNAPSHOT_BANNER = ("STATIC SNAPSHOT · Recorded Muse answers. Quotations were verified against the official BCT PDF "
                   "text when this snapshot was built, not re-verified in your browser. No model and no server are called.")


class SnapshotError(Exception):
    pass


def git_commit() -> tuple[str, bool]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()

    return run("rev-parse", "--short=12", "HEAD"), bool(run("status", "--porcelain", "--", "app", "eval", "data", "scripts"))


def block_network() -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise SnapshotError("the snapshot build must not make network calls")

    httpx.HTTPTransport.handle_request = refuse  # type: ignore[method-assign]
    from app.services.meta_client import MetaModelAdapter

    MetaModelAdapter.complete = refuse  # type: ignore[method-assign]


def export_responses(out: Path) -> dict[str, bytes]:
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services import replay as replay_service

    client = TestClient(create_app())
    bodies: dict[str, bytes] = {}

    def take(path: str, response: httpx.Response) -> None:
        if response.status_code != 200 or not response.headers.get("content-type", "").startswith("application/json"):
            raise SnapshotError(f"{path}: HTTP {response.status_code}; nothing exported")
        bodies[path] = response.content

    take("api/scenarios.json", client.get("/api/scenarios"))
    listed = [s["id"] for s in json.loads(bodies["api/scenarios.json"])]
    if listed != [s.id for s in replay_service.SCENARIOS]:
        raise SnapshotError("the scenario list does not match the scenario registry")
    for sid in listed:
        take(f"api/scenarios/{sid}/replay.json", client.post(f"/api/scenarios/{sid}/replay"))
    take("api/evaluation.json", client.get("/api/evaluation"))
    for rel, body in bodies.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return bodies


def copy_web(out: Path, commit: str, built: str) -> None:
    for name in WEB_FILES:
        shutil.copyfile(WEB_DIR / name, out / name)
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    viewport = '<meta name="viewport" content="width=device-width, initial-scale=1">'
    banner_start = '<div id="mode-banner" class="banner replay" role="status">'
    if html.count(viewport) != 1 or html.count(banner_start) != 1:
        raise SnapshotError("index.html changed shape; update build_snapshot.py")
    html = html.replace(viewport, f'{viewport}\n  <meta name="regulagraph-snapshot" content="static" data-commit="{commit}" '
                                  f'data-built="{built}">')
    start = html.index(banner_start)
    end = html.index("</div>", start)
    html = html[:start] + '<div id="mode-banner" class="banner snapshot" role="status">' + SNAPSHOT_BANNER + html[end:]
    (out / "index.html").write_text(html, encoding="utf-8")


def strings(obj: object):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def check_output(out: Path, bodies: dict[str, bytes], settings: Settings) -> None:
    allowed = set(WEB_FILES) | set(bodies) | {"snapshot-manifest.json"}
    present = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    if present - allowed:
        raise SnapshotError(f"unexpected files in the snapshot: {sorted(present - allowed)}")
    # Credentials: the developer's real .env values (never printed) and anything the app treats as secret.
    env = dotenv_values(ROOT / ".env") if (ROOT / ".env").is_file() else {}
    secrets = [v for k, v in env.items() if v and len(v) >= 8 and any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))]
    from app.core.redaction import configured_secrets

    secrets += [s for s in configured_secrets(settings) if s]
    for rel in present:
        data = (out / rel).read_bytes()
        if data.startswith(b"%PDF"):
            raise SnapshotError(f"{rel}: a PDF must never be exported")
        if any(s.encode() in data for s in secrets):
            raise SnapshotError(f"{rel}: contains a configured credential (value not shown)")
    # Source text: only short exact quotations, never a full provision.
    from app.services.corpus import load_document, load_manifest
    from app.services.provisions import segment_provisions

    texts = [s for body in bodies.values() for s in strings(json.loads(body))]
    too_long = [len(s) for s in texts if len(s) > MAX_STRING_CHARS]
    if too_long:
        raise SnapshotError(f"exported strings longer than {MAX_STRING_CHARS} characters: {too_long}")
    blob = "\n".join(texts)
    checked = 0
    for entry in load_manifest().documents:
        if entry.role != "investigation":
            continue
        for p in segment_provisions(load_document(entry, settings)):
            if len(p.text) >= MIN_PROVISION_CHARS_CHECKED:
                checked += 1
                if p.text in blob:
                    raise SnapshotError(f"the full text of {p.provision_id} appears in the snapshot")
    if checked == 0:
        raise SnapshotError("no provision text was checked; is the corpus loaded?")


def write_manifest(out: Path, commit: str, built: str, dirty: bool) -> dict[str, object]:
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "snapshot-manifest.json":
            data = p.read_bytes()
            files[p.relative_to(out).as_posix()] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    manifest = {
        "kind": "regulagraph static snapshot",
        "commit": commit,
        "working_tree_clean": not dirty,
        "built_at": built,
        "verification": ("Every quotation was located in the hash-verified official BCT PDF text by the real replay "
                         "pipeline when this snapshot was built. The hosted page does not re-verify anything and "
                         "makes no model or server call."),
        "excluded": "No PDFs, no full provision or page text, no credentials, no live mode, no review data.",
        "source_attribution": "Banque Centrale de Tunisie, https://www.bct.gov.tn/ (quotations reproduced exactly).",
        "files": files,
    }
    (out / "snapshot-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def build(out: Path, *, allow_dirty: bool = False, built: str | None = None) -> dict[str, object]:
    import demo  # the same preflight as the local demo

    commit, dirty = git_commit()
    if dirty and not allow_dirty:
        raise SnapshotError("uncommitted changes under app/, eval/, data/ or scripts/; commit first (or --allow-dirty)")
    from app.core.redaction import configured_secrets

    get_settings.cache_clear()
    settings = get_settings()
    if settings.meta_inference_enabled or settings.live_mode_enabled or configured_secrets(settings):
        raise SnapshotError("inference, live mode or a key is configured; the snapshot build refuses to run")
    block_network()
    try:
        demo.check_corpus(settings)
        demo.check_recordings()
        demo.check_replays(settings)
    except demo.PreflightError as exc:
        raise SnapshotError(f"preflight failed: {exc}") from exc
    built = built or datetime.now(UTC).strftime("%Y-%m-%d")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    bodies = export_responses(out)
    copy_web(out, commit + ("-dirty" if dirty else ""), built)
    check_output(out, bodies, settings)
    return write_manifest(out, commit, built, dirty)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (replaced)")
    parser.add_argument("--allow-dirty", action="store_true", help="allow uncommitted changes (marked -dirty)")
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    try:
        manifest = build(out, allow_dirty=args.allow_dirty)
    except SnapshotError as exc:
        print(f"snapshot NOT built: {exc}", file=sys.stderr)
        return 1
    files = manifest["files"]
    assert isinstance(files, dict)
    print(f"snapshot built in {out} from commit {manifest['commit']} ({len(files)} files):")
    for rel, meta in files.items():
        print(f"  {rel:40} {meta['bytes']:>7} bytes  sha256 {meta['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
