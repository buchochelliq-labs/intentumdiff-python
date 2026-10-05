"""Content-type detection bridge + git read-boundary routing.

These exercise the real Rust ``detect_content_type_json`` entry point (so they
assert precise MIME types, not just the NUL-byte fallback).
"""

from __future__ import annotations

from intentumdiff.content_type import detect_content_type, is_text_bytes
from intentumdiff.sources.git_source import _decode_text_or_none

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
    from intentumdiff.differ import SemanticDiffer

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


def test_required_content_engine_failure_propagates(monkeypatch):
    import pytest
    from intentumdiff import rust_core
    def failed():
        raise RuntimeError("missing engine")
    monkeypatch.setattr(rust_core, "_load_backend", failed)
    with pytest.raises(RuntimeError, match="missing engine"):
        detect_content_type(b"plain text")


def test_source_judged_content_corpus():
    import json
    import os
    from pathlib import Path
    import subprocess
    cases = json.loads((Path(__file__).parents[1] / "fixtures/content_routing.json").read_text(encoding="utf-8"))
    for case in cases:
        actual = detect_content_type(bytes(case["bytes"]))
        assert actual["is_text"] is case["is_text"], case["name"]
        if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler":"detect_content_type",**case}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            assert native == actual, case["name"]


def test_malformed_content_result_fails(monkeypatch):
    import pytest
    from intentumdiff import rust_core
    class Backend:
        def detect_content_type_json(self, head):
            return '{"is_text":"false"}'
    monkeypatch.setattr(rust_core, "_load_backend", lambda: Backend())
    with pytest.raises(RuntimeError, match="invalid fields"):
        detect_content_type(b"plain")


def test_content_detection_bounds_bytes_before_abi(monkeypatch):
    import json
    from intentumdiff import rust_core
    from intentumdiff.content_type import HEAD_BYTES

    seen = {}

    class Backend:
        def detect_content_type_json(self, head):
            seen["head"] = head
            return json.dumps(
                {
                    "mime": "application/octet-stream",
                    "extension": "",
                    "category": "binary",
                    "is_text": False,
                }
            )

    monkeypatch.setattr(rust_core, "_load_backend", lambda: Backend())
    payload = b"x" * (HEAD_BYTES + 4096)
    detect_content_type(payload)

    assert len(seen["head"]) == HEAD_BYTES
    assert seen["head"] == payload[:HEAD_BYTES]
