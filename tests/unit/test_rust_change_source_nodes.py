"""Transport-integrity controls for Rust-authored source-anchored changes (#228)."""

from __future__ import annotations

import pytest

from intentumdiff.core.models import Change
from intentumdiff.rust_core import (
    _require_complete_batch_diff_dto,
    _require_source_anchored_changes,
)


@pytest.mark.parametrize("change_type,required", [
    ("ADDITION", ("new_node",)),
    ("DELETION", ("old_node",)),
    ("MODIFICATION", ("old_node", "new_node")),
    ("MOVE", ("old_node", "new_node")),
    ("REORDER", ("old_node", "new_node")),
])
def test_missing_source_node_on_rust_success_is_rejected(change_type, required) -> None:
    for missing in required:
        sample = {"change_type": change_type,
                  **{side: {"id": side} for side in required if side != missing}}
        # Public DTO intentionally supports optional nodes, but Rust transport
        # must not silently manufacture a completed source-less change.
        with pytest.raises(ValueError, match=f"requires {missing} source-node"):
            _require_source_anchored_changes([sample], "Rust unit")


@pytest.mark.parametrize("change", [
    {"change_type": "ADDITION", "new_node": {}},  # omitted inapplicable old_node
    {"change_type": "DELETION", "old_node": {}},  # omitted inapplicable new_node
    {"change_type": "MODIFICATION", "old_node": {}, "new_node": {}},
    {"change_type": "MOVE", "old_node": {}, "new_node": {}},
    {"change_type": "REORDER", "old_node": {}, "new_node": {}},
    {"change_type": "STYLE_ONLY"},  # valid no-node compatibility payload
])
def test_valid_source_evidence_shapes_remain_supported(change) -> None:
    assert _require_source_anchored_changes([change], "Rust unit") == [change]


def _complete(changes):
    return {
        "old_filename": "document.py", "new_filename": "document.py",
        "language": "python", "changes": changes, "change_groups": [],
        "parse_errors": [], "guardrail_violations": [], "metadata": {},
        "has_semantic_changes": bool(changes), "is_style_only": False,
        "is_fallback": False,
    }


def test_truncated_rust_batch_item_cannot_be_normalized_into_success() -> None:
    for kind in ("ADDITION", "DELETION", "MODIFICATION"):
        # Reproduce the Pydantic default that motivated this transport guard.
        decoded = Change.model_validate({"change_type": kind})
        assert decoded.old_node is None and decoded.new_node is None
        with pytest.raises(ValueError, match="requires .*source-node"):
            _require_complete_batch_diff_dto(_complete([{"change_type": kind}]))


def test_real_zero_change_rust_envelope_is_still_valid() -> None:
    assert _require_complete_batch_diff_dto(_complete([]))["changes"] == []
