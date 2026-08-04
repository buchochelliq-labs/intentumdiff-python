"""Content-type detection via the Rust core (magic-byte sniffing).

Thin Python bridge over the Rust ``detect_content_type_json`` entry point. Used
to route changed files — text goes to the semantic parser, binary/image assets
go to the perceptual asset diff — and to enrich diff metadata with the detected
MIME type. Detection inspects the leading bytes of a file, not its extension.

Falls back to a NUL-byte heuristic if the Rust core is unavailable, so callers
never crash when the native extension is missing.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

#: How many leading bytes to sniff. A few KB is plenty for magic-byte detection.
HEAD_BYTES = 8192


class ContentType(TypedDict):
    mime: str
    extension: str
    category: str
    is_text: bool


def _fallback(head: bytes) -> ContentType:
    is_text = b"\x00" not in head[:HEAD_BYTES]
    return {
        "mime": "text/plain" if is_text else "application/octet-stream",
        "extension": "",
        "category": "text" if is_text else "binary",
        "is_text": is_text,
    }


def detect_content_type(head: bytes) -> ContentType:
    """Return the detected content type for a file's leading bytes."""
    sample = bytes(head[:HEAD_BYTES])
    try:
        from intentumdiff.rust_core import _load_backend

        backend = _load_backend()
        raw = backend.detect_content_type_json(sample)
        parsed: dict[str, Any] = json.loads(raw)
        return {
            "mime": str(parsed.get("mime", "application/octet-stream")),
            "extension": str(parsed.get("extension", "")),
            "category": str(parsed.get("category", "binary")),
            "is_text": bool(parsed.get("is_text", False)),
        }
    except Exception:  # noqa: BLE001 — detection must never break the diff pipeline.
        return _fallback(sample)


def is_text_bytes(head: bytes) -> bool:
    """Whether *head* should be sent to the semantic text engine."""
    return detect_content_type(head)["is_text"]
