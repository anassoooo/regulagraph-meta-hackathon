"""Credential redaction for anything that can leave the process (errors, logs, saved results).

``SecretStr`` only protects the settings object itself. A provider can echo a
credential back (for example an "invalid API key: <key>" error message), and
that text would otherwise flow into exceptions, ``InvestigationOutcome`` and
files under ``eval/results/``. Redaction is applied:

* at the provider error boundary (``MetaModelAdapter``),
* when model-call records are built (``investigator._call_model``),
* before evaluation and demo results are written (scripts),
* on the final output of every logging handler (message, ``extra`` fields,
  tracebacks, stack info), once ``install_log_redaction`` has run.

Both the configured secret values and any ``Bearer <token>`` pattern are replaced.
"""

import logging
import re
from collections.abc import Iterable
from typing import Any

from app.core.config import Settings

REDACTED = "[REDACTED]"
MIN_SECRET_LENGTH = 6  # shorter values would redact ordinary words
_BEARER = re.compile(r"(?i)\b(bearer\s+)[^\s\"',;}]+")

_installed_secrets: set[str] = set()


def configured_secrets(settings: Settings) -> list[str]:
    key = settings.meta_api_key.get_secret_value() if settings.meta_api_key is not None else ""
    return [key] if len(key) >= MIN_SECRET_LENGTH else []


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted({s for s in secrets if len(s) >= MIN_SECRET_LENGTH}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return _BEARER.sub(lambda m: m.group(1) + REDACTED, text)


def redact_obj(value: Any, secrets: Iterable[str]) -> Any:
    """Recursively redact strings inside dicts, lists and tuples (keys included)."""
    secrets = list(secrets)
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {redact_obj(k, secrets): redact_obj(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_obj(v, secrets) for v in value)
    return value


def install_log_redaction(settings: Settings) -> None:
    """Redact configured secrets and bearer tokens from all log output from now on.

    Two layers, both process-wide and idempotent:

    1. Output boundary (authoritative): ``logging.Handler.format`` is wrapped at
       class level, so the final string every handler emits is redacted after
       the formatter has added the message, ``extra`` fields, the traceback
       (including chained exceptions) and stack info. This covers all handlers
       that build output through ``self.format(record)``: stream, file,
       rotating, queue, uvicorn's and pytest's handlers and ``lastResort``.
    2. Record creation (early): the message of each new ``LogRecord`` is
       redacted, which also protects handlers that read ``record.getMessage()``
       directly instead of formatting.
    """
    _installed_secrets.update(configured_secrets(settings))
    _install_handler_output_redaction()
    factory = logging.getLogRecordFactory()
    if getattr(factory, "_regulagraph_redacting", False):
        return

    def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # malformed format args: leave the record to logging's own error path
            return record
        cleaned = redact(message, _installed_secrets)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return record

    redacting_factory._regulagraph_redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(redacting_factory)


def _install_handler_output_redaction() -> None:
    original = logging.Handler.format
    if getattr(original, "_regulagraph_redacting", False):
        return

    def redacting_format(self: logging.Handler, record: logging.LogRecord) -> str:
        return redact(original(self, record), _installed_secrets)

    redacting_format._regulagraph_redacting = True  # type: ignore[attr-defined]
    logging.Handler.format = redacting_format  # type: ignore[method-assign]
