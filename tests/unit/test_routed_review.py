"""Source expectations and identical-input native/C ABI review comparisons."""
import json
import os
from pathlib import Path
import subprocess

import pytest

import intentumdiff.rust_core as rust_core
from intentumdiff.core.models import Change, ChangeType, NodePosition, SemanticDiff, SemanticNode

from intentumdiff.rust_core import _c_abi_call


def test_routed_review_source_corpus():
    cases = json.loads((Path(__file__).parents[1] / "fixtures/routed_review.json").read_text(encoding="utf-8"))
    for case in cases:
        result = _c_abi_call("complete_routed_review", json.dumps(case["request"]))
        assert result["has_semantic_changes"] == case["semantic"]
        assert result["is_style_only"] == case["style"]
        assert len(result["changes"]) == case["changes"]
        if "meaningful_indices" in case:
            group = next(g for g in result["change_groups"] if g["kind"] == "MEANINGFUL_CHANGE")
            assert group["raw_change_indices"] == case["meaningful_indices"]
            assert group["old_labels"] == ["2"]
            assert group["new_labels"] == ["3"]
        if case.get("no_style_evidence"):
            assert "ignored_style_changes" not in result["metadata"]
        if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler":"complete_routed_review", "request":case["request"]}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            assert result == native, case["name"]


def test_routed_review_rejects_incomplete_rust_success_without_pydantic_defaults(monkeypatch):
    """Reject missing inventories and coerced flags through the actual routed adapter."""
    old = SemanticNode(
        id="old", node_type="literal", label="1", structural_hash="old",
        position=NodePosition(start_line=0, start_col=4, end_line=0, end_col=5),
    )
    new = SemanticNode(
        id="new", node_type="literal", label="2", structural_hash="new",
        position=NodePosition(start_line=0, start_col=4, end_line=0, end_col=5),
    )
    change = Change(change_type=ChangeType.MODIFICATION, old_node=old, new_node=new)
    finalized = {"changes": [change], "change_groups": [], "is_style_only": False}
    full = SemanticDiff(
        old_filename="edit.py", new_filename="edit.py", language="python",
        changes=[change], has_semantic_changes=True,
        metadata={"engine_owner": "rust"},
    ).model_dump(mode="json")

    response = full

    def mock_call(handler, request):
        assert handler == "complete_routed_review"
        request = json.loads(request)
        assert request["finalized"]["changes"]
        assert request["old_source"] == "x = 1"
        assert request["new_source"] == "x = 2"
        return response

    monkeypatch.setattr(rust_core, "_c_abi_call", mock_call)

    def invoke():
        return rust_core.complete_routed_review(
            finalized=finalized, old_tree=old, new_tree=new,
            old_source="x = 1", new_source="x = 2",
            old_filename="edit.py", new_filename="edit.py", language="python",
        )

    valid = invoke()
    assert valid.has_semantic_changes is True
    assert valid.is_style_only is False
    assert len(valid.changes) == 1
    assert valid.changes[0].new_node.position.start_col == 4

    for field in (
        "changes", "change_groups", "parse_errors", "guardrail_violations",
        "has_semantic_changes", "is_style_only", "is_fallback", "metadata",
    ):
        response = dict(full)
        response.pop(field)
        with pytest.raises(ValueError, match=f"Rust complete batch diff requires {field}"):
            invoke()

    for field in ("changes", "change_groups", "parse_errors", "guardrail_violations"):
        for malformed in (None, {}, "[]"):
            response = {**full, field: malformed}
            with pytest.raises(ValueError, match=f"Rust complete batch diff requires {field}"):
                invoke()

    for field in ("has_semantic_changes", "is_style_only", "is_fallback"):
        for malformed in ("false", 0, None):
            response = {**full, field: malformed}
            with pytest.raises(ValueError, match=f"Rust complete batch diff requires {field}"):
                invoke()

    for malformed in (None, [], "{}", 1):
        response = {**full, "metadata": malformed}
        with pytest.raises(ValueError, match="Rust complete batch diff requires metadata"):
            invoke()

    # Rust can explicitly send empty inventories with false flags, but it must
    # do so by filling the complete DTO rather than relying on Python defaults.
    response = SemanticDiff(
        old_filename="edit.py", new_filename="edit.py", language="python",
        metadata={"engine_owner": "rust"},
    ).model_dump(mode="json")
    empty = invoke()
    assert empty.changes == [] and empty.change_groups == []
    assert not empty.has_semantic_changes and not empty.is_style_only and not empty.is_fallback
