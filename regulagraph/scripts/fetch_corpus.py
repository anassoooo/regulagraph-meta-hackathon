"""Download the corpus PDFs listed in data/manifest.json and verify their SHA-256.

    python scripts/fetch_corpus.py            # all documents
    python scripts/fetch_corpus.py bct-2026-10-fr

Files go to data/raw/ (git-ignored). A download larger than MAX_PDF_BYTES is
aborted; a file whose hash differs from the manifest is deleted and reported.
"""

import hashlib
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest  # noqa: E402


def fetch(keys: list[str]) -> int:
    settings = Settings()
    corpus = load_manifest()
    entries = [corpus.get(k) for k in keys] if keys else corpus.documents
    DEFAULT_RAW_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for entry in entries:
            target = DEFAULT_RAW_DIR / entry.filename
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == entry.sha256:
                print(f"ok      {entry.key} (already present)")
                continue
            digest = hashlib.sha256()
            size = 0
            tmp = target.with_suffix(".part")
            with client.stream("GET", str(entry.source_url)) as response, tmp.open("wb") as out:
                if response.status_code != 200:
                    print(f"FAILED  {entry.key}: HTTP {response.status_code}")
                    failures += 1
                    continue
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > settings.max_pdf_bytes:
                        break
                    digest.update(chunk)
                    out.write(chunk)
            if size > settings.max_pdf_bytes:
                tmp.unlink(missing_ok=True)
                print(f"FAILED  {entry.key}: larger than MAX_PDF_BYTES={settings.max_pdf_bytes}")
                failures += 1
            elif digest.hexdigest() != entry.sha256:
                tmp.unlink(missing_ok=True)
                print(f"FAILED  {entry.key}: sha256 {digest.hexdigest()} != manifest {entry.sha256}")
                failures += 1
            else:
                tmp.replace(target)
                print(f"ok      {entry.key} ({size} bytes)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(fetch(sys.argv[1:]))
