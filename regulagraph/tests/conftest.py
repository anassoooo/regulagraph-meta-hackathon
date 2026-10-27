from collections.abc import Callable

import pymupdf
import pytest

from app.core.config import Settings


@pytest.fixture
def make_pdf() -> Callable[..., bytes]:
    """Build a small synthetic PDF in memory; one string per page (None = blank page)."""

    def _make(pages: list[str | None], *, title: str = "", password: str | None = None) -> bytes:
        pdf = pymupdf.open()
        for text in pages:
            page = pdf.new_page()
            if text:
                page.insert_text((72, 72), text)
        pdf.set_metadata({"title": title})
        kwargs = {}
        if password:
            kwargs = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password}
        data = pdf.tobytes(**kwargs)
        pdf.close()
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
