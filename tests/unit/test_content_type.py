"""Content-type detection bridge + git read-boundary routing.

These exercise the real Rust ``detect_content_type_json`` entry point (so they
assert precise MIME types, not just the NUL-byte fallback).
"""

from __future__ import annotations

from intentdiff.content_type import detect_content_type, is_text_bytes
from intentdiff.sources.git_source import _decode_text_or_none

_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def test_png_bytes_detected_as_binary_image() -> None:
    ct = detect_content_type(_PNG)
    assert ct["mime"] == "image/png"
    assert ct["category"] == "image"
    assert ct["is_text"] is False


def test_utf8_source_is_text() -> None:
    ct = detect_content_type(b"def foo():\n    return 1\n")
    assert ct["is_text"] is True
    assert ct["category"] == "text"
    assert is_text_bytes(b"plain text") is True


def test_nul_bytes_are_binary() -> None:
    assert is_text_bytes(b"text\x00then binary") is False


def test_diff_metadata_includes_content_type() -> None:
    from intentdiff.differ import SemanticDiffer

    diff = SemanticDiffer().diff_strings(
        "def a():\n    return 1\n", "def a():\n    return 2\n", "x.py"
    )
    ct = (diff.metadata or {}).get("content_type")
    assert ct is not None
    assert ct["is_text"] is True
    assert ct["category"] == "text"


def test_decode_text_or_none_routes_by_content() -> None:
    # Binary/image content is dropped (routed to the asset diff, not the parser).
    assert _decode_text_or_none(_PNG) is None
    # Text decodes through.
    assert _decode_text_or_none(b"def a():\n    pass\n") == "def a():\n    pass\n"
    # Empty (added/deleted side) is valid empty text.
    assert _decode_text_or_none(b"") == ""
