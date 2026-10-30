# Licensing notes

> This is an engineering record, not legal advice. Check the hackathon's licence rules
> before submission.

## Parser decision

Sprint 1 first used **PyMuPDF** (dual-licensed AGPL-3.0 or Artifex commercial). After PM
review of PR #3 it was replaced with **pypdf**, which is permissively licensed. That
removes the AGPL obligations on the combined work, including the §13 duty to offer source
to network users. PyMuPDF is no longer a dependency, and nothing in `app/` or `tests/`
imports it.

## Runtime dependencies (verified)

Licences below were read from the installed packages' metadata (`License-Expression` /
`License`) on 2026-10-27. They cover the full runtime closure of `pip install .`, with
dev tools such as pytest excluded.

| Package | Version | Licence | Pulled in by |
|---|---|---|---|
| pypdf | 6.21.2 | BSD-3-Clause | direct (`pypdf[crypto]`) |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | pypdf `crypto` extra (AES-encrypted PDFs) |
| cffi | 2.1.1 | MIT-0 | cryptography |
| pycparser | 3.12 | BSD-3-Clause | cffi |
| fastapi | 0.162.0 | MIT | direct |
| starlette | 1.10.0 | BSD-3-Clause | fastapi |
| annotated-doc | 0.0.5 | MIT | fastapi |
| opentelemetry-api | 1.45.1 | Apache-2.0 | fastapi |
| uvicorn | 0.54.0 | BSD-3-Clause | direct |
| click | 8.5.0 | BSD-3-Clause | uvicorn |
| h11 | 0.16.0 | MIT | uvicorn, httpcore |
| pydantic | 2.14.1 | MIT | direct |
| pydantic-core | 2.50.1 | MIT | pydantic |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-inspection | 0.4.4 | MIT | pydantic, fastapi |
| typing-extensions | 4.16.0 | PSF-2.0 | many (pydantic, fastapi, starlette, cryptography, …) |
| pydantic-settings | 2.16.1 | MIT | direct |
| python-dotenv | 1.2.4 | BSD-3-Clause | pydantic-settings |
| httpx | 0.28.1 | BSD-3-Clause | direct |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| anyio | 4.15.1 | MIT | httpx, starlette |
| idna | 3.20 | BSD-3-Clause | httpx, anyio |
| certifi | 2026.10.23 | MPL-2.0 | httpx, httpcore |

Versions are the ones resolved in the development venv. `pyproject.toml` sets minimum
versions, and pypdf is pinned below 7.

### Obligations that remain

- **Permissive licences (MIT, BSD-3-Clause, Apache-2.0, PSF-2.0, MIT-0):** if we
  redistribute the packages (for example, bundled in a container image), keep their
  copyright and licence notices. Apache-2.0 also requires keeping any NOTICE file. Hosting
  a service does not trigger source-disclosure duties.
- **certifi (MPL-2.0):** weak, file-level copyleft. It only applies if we modify certifi's
  own files, and we don't. Keep its licence notice when redistributing.

## Still open: the project's own licence

The repository has **no LICENSE file**, so RegulaGraph's own code currently has no stated
licence. Because every dependency is permissive, the team can choose any licence
(MIT, Apache-2.0, …). Check the hackathon terms, and confirm that contributors may release
the code. This is a team decision and is not made here.

## Re-checking

Run this after any dependency change, and update the table:

```bash
python -c "import importlib.metadata as m; [print(d.metadata['Name'], d.version, d.metadata.get('License-Expression') or d.metadata.get('License')) for d in m.distributions()]"
```
