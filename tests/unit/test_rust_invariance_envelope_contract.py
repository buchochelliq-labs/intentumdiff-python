"""Source-judged Rust invariance transport contract (0.0.2).

The pinned native implementation returns changes, change_groups and
ignored_style_changes arrays for apply, style_only and zero_change_literal,
including valid empty results. Python must never fabricate those arrays when
a malformed native response arrives.
"""

import json
from types import SimpleNamespace

import pytest

from intentumdiff import rust_core


def _stub_invariance_result(monkeypatch, payload):
    calls = []

    def run(request):
        calls.append(json.loads(request))
        return json.dumps(payload)

    monkeypatch.setattr(
        rust_core,
        "_load_backend",
        lambda: SimpleNamespace(apply_invariances_json=run),
    )
    return calls


@pytest.mark.parametrize("mode", ["apply", "style_only", "zero_change_literal"])
def test_pinned_rust_empty_result_is_accepted(monkeypatch, mode):
    calls = _stub_invariance_result(
        monkeypatch,
        {"changes": [], "change_groups": [], "ignored_style_changes": []},
    )
    result = rust_core._run_invariance_request({"mode": mode})
    assert result.changes == []
    assert result.change_groups == []
    assert result.ignored_style_changes == []
    assert calls == [{"mode": mode}]


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({}, "changes must be an array"),
        ({"changes": []}, "change_groups must be an array"),
        (
            {"changes": [], "change_groups": []},
            "ignored_style_changes must be an array",
        ),
        (
            {"changes": None, "change_groups": [], "ignored_style_changes": []},
            "changes must be an array",
        ),
        (
            {"changes": {}, "change_groups": [], "ignored_style_changes": []},
            "changes must be an array",
        ),
        (
            {"changes": [], "change_groups": None, "ignored_style_changes": []},
            "change_groups must be an array",
        ),
        (
            {"changes": [], "change_groups": [], "ignored_style_changes": {}},
            "ignored_style_changes must be an array",
        ),
        (
            {"changes": [], "change_groups": [], "ignored_style_changes": [None]},
            "ignored_style_changes entries must be objects",
        ),
        (
            {"changes": [], "change_groups": [], "ignored_style_changes": ["ignored"]},
            "ignored_style_changes entries must be objects",
        ),
    ],
)
def test_malformed_rust_result_never_fabricates_empty_success(monkeypatch, result, message):
    _stub_invariance_result(monkeypatch, result)
    with pytest.raises(RuntimeError, match=message):
        rust_core._run_invariance_request({"mode": "apply"})


@pytest.mark.parametrize("payload", [None, [], "unexpected"])
def test_non_object_rust_result_is_rejected(monkeypatch, payload):
    _stub_invariance_result(monkeypatch, payload)
    with pytest.raises(RuntimeError, match="non-object payload"):
        rust_core._run_invariance_request({"mode": "apply"})


def test_rust_style_evidence_entries_are_preserved(monkeypatch):
    evidence = {"rule_id": "generic.style_only_shortcut.source_equivalence", "marker": "native"}
    _stub_invariance_result(
        monkeypatch,
        {"changes": [], "change_groups": [], "ignored_style_changes": [evidence]},
    )
    result = rust_core._run_invariance_request({"mode": "style_only"})
    assert result.ignored_style_changes == [evidence]
