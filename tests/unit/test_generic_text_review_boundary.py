"""Rust generic-text review must never manufacture successful empty results."""

import json
from types import SimpleNamespace

import pytest

from intentumdiff import rust_core


def _invoke(monkeypatch, backend):
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    return rust_core.try_rust_generic_text_review("before", "after", 1)


def test_missing_generic_text_handler_fails_closed(monkeypatch):
    with pytest.raises(RuntimeError, match="required Rust generic-text review handler"):
        _invoke(monkeypatch, SimpleNamespace())


@pytest.mark.parametrize("payload", [
    {}, [], {"used": None}, {"used": "false"}, {"used": 1},
    {"used": True}, {"used": True, "changes": None},
    {"used": True, "changes": {}}, {"used": True, "changes": [None]},
])
def test_malformed_generic_text_envelope_fails_closed(monkeypatch, payload):
    backend = SimpleNamespace(generic_text_review_json=lambda *_: json.dumps(payload))
    with pytest.raises(RuntimeError, match="Rust generic-text review failed"):
        _invoke(monkeypatch, backend)


def test_explicit_rust_decline_is_not_fabricated_success(monkeypatch):
    backend = SimpleNamespace(generic_text_review_json=lambda *_: '{"used": false}')
    assert _invoke(monkeypatch, backend) is None


def test_valid_empty_rust_result_is_preserved(monkeypatch):
    backend = SimpleNamespace(generic_text_review_json=lambda *_: '{"used": true, "changes": [], "group": null}')
    assert _invoke(monkeypatch, backend) == ([], [])


def test_native_generic_text_error_propagates(monkeypatch):
    def fail(*_):
        raise OSError("native failure")

    backend = SimpleNamespace(generic_text_review_json=fail)
    with pytest.raises(RuntimeError, match="native failure"):
        _invoke(monkeypatch, backend)
