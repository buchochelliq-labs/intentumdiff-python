"""Host metadata must never fabricate source bytes before Rust classification (#254)."""

from __future__ import annotations

import pytest

from intentumdiff._differ_runtime import _attach_content_type_metadata


class _DiffDTO:
    """Only the presentation metadata contract exercised by this adapter."""

    def __init__(self, metadata=None):
        self.metadata = metadata or {"existing": "retained"}

    def model_copy(self, *, update):
        return _DiffDTO(update["metadata"])


@pytest.mark.parametrize("bad_codepoint", [chr(0xDCFF), chr(0xDCFE)])
def test_content_type_metadata_rejects_undecodable_source_before_rust(
    monkeypatch, bad_codepoint
):
    from intentumdiff import content_type

    called = []
    monkeypatch.setattr(
        content_type, "detect_content_type",
        lambda head: called.append(head),
    )
    with pytest.raises(UnicodeEncodeError, match="surrogates not allowed"):
        _attach_content_type_metadata(_DiffDTO(), "", "x" + bad_codepoint)
    assert called == [], "Rust must never receive lossy replacement bytes"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("", "π = 2\n", "π = 2\n"),
        ("á = 1\n", "", "á = 1\n"),
    ],
)
def test_content_type_metadata_preserves_valid_unicode_bytes(
    monkeypatch, old, new, expected
):
    from intentumdiff import content_type

    seen = []
    classified = {
        "mime": "text/plain",
        "extension": "txt",
        "category": "text",
        "is_text": True,
    }

    def classify(head):
        seen.append(head)
        return classified

    monkeypatch.setattr(content_type, "detect_content_type", classify)
    result = _attach_content_type_metadata(_DiffDTO(), old, new)
    assert seen == [expected.encode("utf-8")]
    assert result.metadata["existing"] == "retained"
    assert result.metadata["content_type"] == classified


def test_content_type_metadata_propagates_required_engine_failure(monkeypatch):
    from intentumdiff import content_type

    def unavailable(head):
        raise RuntimeError("required Rust content detector missing")

    monkeypatch.setattr(content_type, "detect_content_type", unavailable)
    with pytest.raises(RuntimeError, match="required Rust content detector missing"):
        _attach_content_type_metadata(_DiffDTO(), "", "value = 1\n")
