import io
from collections.abc import Callable

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import Settings


def _pdf_string(line: str) -> bytes:
    escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("latin-1") + b")"


def build_pdf(pages: list[str | None], *, title: str = "") -> bytes:
    """Write a minimal, byte-deterministic PDF: one Helvetica text block per page."""
    page_count = len(pages)
    font_obj = 3
    first_page_obj = 4  # page i -> object 4 + 2i, its content stream -> 5 + 2i
    info_obj = first_page_obj + 2 * page_count

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % (first_page_obj + 2 * i) for i in range(page_count))
        + b"] /Count %d >>" % page_count,
        font_obj: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        info_obj: b"<< /Title " + _pdf_string(title) + b" >>",
    }
    for i, text in enumerate(pages):
        page_obj, content_obj = first_page_obj + 2 * i, first_page_obj + 2 * i + 1
        objects[page_obj] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" % (font_obj, content_obj)
        )
        ops = b""
        if text:
            lines = text.split("\n")
            ops = b"BT /F1 12 Tf 14 TL 72 720 Td " + _pdf_string(lines[0]) + b" Tj"
            for line in lines[1:]:
                ops += b" T* " + _pdf_string(line) + b" Tj"
            ops += b" ET"
        objects[content_obj] = b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream"

    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, info_obj, xref_at)
    return bytes(out)


def encrypt_pdf(data: bytes, *, user_password: str, owner_password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def make_pdf() -> Callable[..., bytes]:
    """Build a synthetic PDF in memory; one string per page (None = blank page).

    ``password`` sets a user password (cannot be opened without it);
    ``owner_password`` alone restricts permissions but leaves the file readable.
    """

    def _make(
        pages: list[str | None],
        *,
        title: str = "",
        password: str | None = None,
        owner_password: str | None = None,
    ) -> bytes:
        data = build_pdf(pages, title=title)
        if password or owner_password:
            data = encrypt_pdf(data, user_password=password or "", owner_password=owner_password or password or "")
        return data

    return _make


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    """Settings isolated from the developer's .env and real credentials."""

    def _make(**overrides: object) -> Settings:
        values: dict[str, object] = {"meta_api_key": None, "meta_inference_enabled": False}
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return _make
