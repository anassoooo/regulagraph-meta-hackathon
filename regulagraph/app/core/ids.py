"""Deterministic source identifiers and content digests.

IDs are derived only from content and position, so re-ingesting the same
PDF always yields the same document, page, provision and evidence IDs.

ID inputs are hashed via canonical JSON (typed, quoted, escaped, nested
lists kept as lists), never by joining strings with a delimiter, so no choice
of field values can make two different inputs serialise identically.
IDs keep 128 bits of SHA-256; full 256-bit digests are used for content binding.
"""

import hashlib
import json
from typing import Any, TypeAlias

ID_SCHEME = "regulagraph.id.v1"
_ID_HEX_CHARS = 32  # 128 bits

IdPart: TypeAlias = "str | int | list[IdPart]"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8 text, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_digest(value: Any) -> str:
    """Full SHA-256 hex digest of the canonical JSON form of ``value``."""
    return sha256_hex(canonical_json(value).encode("utf-8"))


def _check_part(part: object) -> None:
    # bool is an int subclass; reject it so True never aliases 1.
    if isinstance(part, bool) or not isinstance(part, str | int | list):
        raise TypeError(f"unsupported id part type: {type(part).__name__}")
    if isinstance(part, list):
        for item in part:
            _check_part(item)


def stable_id(prefix: str, *parts: IdPart) -> str:
    """``prefix_<32 hex>`` from SHA-256 over canonical JSON of (scheme, prefix, parts)."""
    for part in parts:
        _check_part(part)
    return f"{prefix}_{content_digest([ID_SCHEME, prefix, list(parts)])[:_ID_HEX_CHARS]}"


def document_id(content_sha256: str) -> str:
    return f"doc_{content_sha256[:_ID_HEX_CHARS]}"


DOCUMENT_ID_PATTERN = rf"^doc_[0-9a-f]{{{_ID_HEX_CHARS}}}$"


def page_id(doc_id: str, page_number: int) -> str:
    # Unambiguous: doc_id is constrained to DOCUMENT_ID_PATTERN (no ':') and page_number is an int.
    return f"{doc_id}:p{page_number}"
