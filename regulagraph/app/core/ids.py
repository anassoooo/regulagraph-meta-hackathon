"""Deterministic source identifiers.

IDs are derived only from content and position, so re-ingesting the same
PDF always yields the same document, page, provision and evidence IDs.
"""

import hashlib


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, *parts: str | int) -> str:
    """``prefix_<16 hex>`` from a SHA-256 over the unit-separator-joined parts."""
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return f"{prefix}_{sha256_hex(payload)[:16]}"


def document_id(content_sha256: str) -> str:
    return f"doc_{content_sha256[:16]}"


def page_id(doc_id: str, page_number: int) -> str:
    return f"{doc_id}:p{page_number}"
