"""Content-type detection via the Rust core (magic-byte sniffing).

Thin Python bridge over the Rust ``detect_content_type_json`` entry point. Used
to route changed files — text goes to the semantic parser, binary/image assets
go to the perceptual asset diff — and to enrich diff metadata with the detected
MIME type. Detection inspects the leading bytes of a file, not its extension.

A missing or failing Rust engine raises; routing never uses a Python fallback.
"""

from __future__ import annotations

from typing import TypedDict

#: How many leading bytes to sniff. A few KB is plenty for magic-byte detection.
HEAD_BYTES = 8192


class ContentType(TypedDict):
    mime: str
    extension: str
    category: str
    is_text: bool


def detect_content_type(head: bytes) -> ContentType:
    """Return Rust's detected content type; propagate required engine failures."""
    from intentumdiff.rust_core import _required_engine_json

    result = _required_engine_json(
        "detect_content_type_json",
        bytes(head[:HEAD_BYTES]),
        result_type=dict,
    )
    if any(not isinstance(result.get(key), str) for key in ("mime", "extension", "category")) or type(result.get("is_text")) is not bool:
        raise RuntimeError("Rust content-type result has invalid fields")
    return ContentType(**{key: result[key] for key in ContentType.__annotations__})


def is_text_bytes(head: bytes) -> bool:
    """Whether *head* should be sent to the semantic text engine."""
    return detect_content_type(head)["is_text"]
