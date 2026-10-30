"""Credentials never leave the process: provider errors, logs, outcomes and saved results."""

import json
import logging

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ModelProviderError
from app.core.redaction import REDACTED, install_log_redaction, redact, redact_obj
from app.services.meta_client import ChatMessage, MetaModelAdapter

DUMMY_KEY = "dummy-secret-key-0123456789abcdef"


def _echoing_transport(status: int = 401) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        return httpx.Response(status, json={"error": {
            "message": f"Invalid API key provided: {DUMMY_KEY}. Header was '{auth}'.",
            "type": f"invalid_request_error ({DUMMY_KEY})", "param": None, "code": "invalid_api_key"}})

    return httpx.MockTransport(handler)


@pytest.fixture
def settings(settings_factory) -> Settings:
    return settings_factory(meta_api_key=DUMMY_KEY, meta_inference_enabled=True)


def test_redact_helpers() -> None:
    assert redact(f"key={DUMMY_KEY}", [DUMMY_KEY]) == f"key={REDACTED}"
    assert redact("Authorization: Bearer abc.def-123", []) == f"Authorization: Bearer {REDACTED}"
    assert redact("short", ["abc"]) == "short"  # very short values are not treated as secrets
    assert redact_obj({DUMMY_KEY: [DUMMY_KEY, ("x", DUMMY_KEY)]}, [DUMMY_KEY]) == {REDACTED: [REDACTED, ("x", REDACTED)]}


def test_provider_error_echoing_the_key_is_redacted_at_the_boundary(settings) -> None:
    adapter = MetaModelAdapter(settings, transport=_echoing_transport())
    with pytest.raises(ModelProviderError) as err:
        adapter.complete([ChatMessage(role="user", content="x")])
    dumped = json.dumps(err.value.to_dict()) + str(err.value) + repr(err.value)
    assert DUMMY_KEY not in dumped and "Bearer dummy" not in dumped
    assert REDACTED in err.value.message and err.value.status_code == 401


def _corpus(make_pdf):
    from app.core import ids
    from app.services.corpus import CorpusEntry, attach_metadata
    from app.services.document_parser import extract_pdf_bytes
    from app.services.provisions import segment_provisions

    data = make_pdf(["Article 1 : Objet.\nArticle 2 : La presente circulaire abroge la circulaire n 2018-16."])
    entry = CorpusEntry(key="new", reference="Circulaire n°2026-99", title="t", publication_date="2026-01-01",
                        language="fr", role="investigation", source_url="https://example.org/x.pdf",
                        filename="new.pdf", sha256=ids.sha256_hex(data), bytes=len(data), retrieved_on="2026-10-30",
                        pages=1)
    doc = attach_metadata(extract_pdf_bytes(data, filename="n.pdf"), entry)
    return {doc.document.document_id: doc}, {doc.document.document_id: segment_provisions(doc)}


def test_dummy_credential_never_reaches_outcome_or_logs(settings, make_pdf, caplog) -> None:
    from app.services.investigator import investigate

    install_log_redaction(settings)
    sources, provisions = _corpus(make_pdf)
    adapter = MetaModelAdapter(settings, transport=_echoing_transport())
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("app.test").warning("provider said: %s", f"Invalid API key provided: {DUMMY_KEY}")
        outcome = investigate("Quelle circulaire est abrogee ?", sources, provisions, adapter, settings)
    assert outcome.model_call.outcome == "provider_error" and outcome.model_call.http_status == 401
    serialized = outcome.model_dump_json()
    assert DUMMY_KEY not in serialized and REDACTED in serialized
    assert DUMMY_KEY not in caplog.text and "provider said: Invalid API key provided: [REDACTED]" in caplog.text


def test_saved_results_are_redacted(monkeypatch, tmp_path) -> None:
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("MODEL_API_KEY", DUMMY_KEY)  # the writer reads configured secrets from settings
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "RESULTS", tmp_path)
    out = module._write("live", {"error": {"message": f"bad key {DUMMY_KEY}", "auth": "Bearer abcdef123456"},
                                 DUMMY_KEY: [DUMMY_KEY]})
    text = out.read_text(encoding="utf-8")
    assert DUMMY_KEY not in text and "abcdef123456" not in text and REDACTED in text


def test_live_runner_end_to_end_with_echoing_provider_saves_no_credential(monkeypatch, tmp_path) -> None:
    """Full run_live path (preflight -> adapter -> provider error -> systemic stop -> saved file)."""
    import importlib.util
    from pathlib import Path

    from app.services.corpus import DEFAULT_RAW_DIR, load_manifest
    from app.services.evaluation import load_corpus_view

    if not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"):
        pytest.skip("corpus PDFs not downloaded")
    monkeypatch.setenv("MODEL_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("META_INFERENCE_ENABLED", "true")
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "RESULTS", tmp_path)
    monkeypatch.setattr(module, "MetaModelAdapter", lambda s: MetaModelAdapter(s, transport=_echoing_transport()))
    settings = Settings(_env_file=None)
    assert settings.meta_api_key.get_secret_value() == DUMMY_KEY  # the dummy, never the real key
    view = load_corpus_view(settings)
    monkeypatch.setattr(module, "load_corpus_view", lambda s: view)
    assert module.run_live(settings, confirmed=True, only=["E01", "E05"], reasoning_effort="low") == 0
    (saved,) = list(tmp_path.glob("live_*.json"))
    text = saved.read_text(encoding="utf-8")
    assert DUMMY_KEY not in text and REDACTED in text
    data = json.loads(text)
    assert data["summary"]["api_calls"] == 1  # stopped after the systemic provider error; E05 never called
    assert data["results"][0]["model_call"]["outcome"] == "provider_error"


# Final emitted log output (second review of fd9c67d) ---------------------------

BEARER = "Bearer live-token-abcdef123456"


@pytest.fixture
def emitted(settings, tmp_path):
    """A logger with a stream handler and a file handler; returns a function giving all emitted text."""
    import io

    install_log_redaction(settings)
    logger = logging.getLogger(f"regulagraph.test.redaction.{tmp_path.name}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    stream = io.StringIO()
    fmt = logging.Formatter("%(levelname)s %(message)s %(provider_error)s")
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(fmt)
    file_handler = logging.FileHandler(tmp_path / "app.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

    def text() -> str:
        file_handler.flush()
        return stream.getvalue() + "\n" + (tmp_path / "app.log").read_text(encoding="utf-8")

    yield logger, text
    for h in (stream_handler, file_handler):
        logger.removeHandler(h)
        h.close()


def _log_with_stack(logger, marker):
    logger.warning("with stack", stack_info=True, extra={"provider_error": "-"})  # noqa: E501


def test_exception_traceback_is_redacted(emitted) -> None:
    logger, text = emitted
    try:
        raise RuntimeError(DUMMY_KEY)
    except RuntimeError:
        logger.exception("request failed", extra={"provider_error": "-"})
    out = text()
    assert "request failed" in out and "RuntimeError: [REDACTED]" in out and "Traceback" in out
    assert DUMMY_KEY not in out


def test_extra_field_is_redacted(emitted) -> None:
    logger, text = emitted
    logger.warning("request failed", extra={"provider_error": DUMMY_KEY})
    logger.warning("request failed", extra={"provider_error": f"header {BEARER}"})
    out = text()
    assert DUMMY_KEY not in out and "live-token-abcdef123456" not in out
    assert "WARNING request failed [REDACTED]" in out and "Bearer [REDACTED]" in out


def test_chained_exception_is_redacted(emitted) -> None:
    logger, text = emitted
    try:
        try:
            raise ValueError(f"upstream rejected {BEARER}")
        except ValueError as inner:
            raise RuntimeError(f"wrapped {DUMMY_KEY}") from inner
    except RuntimeError:
        logger.error("chained", exc_info=True, extra={"provider_error": "-"})
    out = text()
    assert "direct cause of the following exception" in out  # both exceptions were formatted
    assert DUMMY_KEY not in out and "live-token-abcdef123456" not in out


def test_stack_info_is_redacted(emitted) -> None:
    logger, text = emitted
    # The stack shows this source line, which contains the dummy key literal below.
    _log_with_stack(logger, "dummy-secret-key-0123456789abcdef")
    out = text()
    assert "Stack (most recent call last)" in out and "_log_with_stack(logger, " in out
    assert DUMMY_KEY not in out


def test_handlers_configured_later_and_custom_formatters_are_covered(settings, tmp_path) -> None:
    """The boundary is Handler.format, so handlers added after installation are protected too."""
    import io

    class JsonishFormatter(logging.Formatter):
        def format(self, record):
            return json.dumps({"msg": record.getMessage(), "err": getattr(record, "provider_error", None)})

    install_log_redaction(settings)
    logger = logging.getLogger("regulagraph.test.redaction.later")
    logger.propagate = False
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonishFormatter())
    logger.addHandler(handler)
    try:
        logger.warning("x", extra={"provider_error": DUMMY_KEY})
    finally:
        logger.removeHandler(handler)
    assert DUMMY_KEY not in stream.getvalue() and REDACTED in stream.getvalue()


def test_ordinary_log_output_is_unchanged(emitted) -> None:
    logger, text = emitted
    logger.info("loaded %d provisions", 80, extra={"provider_error": "none"})
    assert "INFO loaded 80 provisions none" in text()
