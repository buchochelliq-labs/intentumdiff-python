"""Regression guards for semantic subtree parity in the 0.0.2 release gate."""
from __future__ import annotations

import runpy
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_migration_parity.py"


def _projection(diff):
    return runpy.run_path(str(SCRIPT))["projection"](diff)


def _node(label: str, *, children=None, col: int = 0):
    result = {
        "node_type": "identifier",
        "label": label,
        "position": {"start_line": 0, "start_col": col, "end_line": 0, "end_col": col + len(label)},
        "id": "nonsemantic-random-id",
    }
    if children is not None:
        result["children"] = children
    return result


def _diff(node):
    return {
        "changes": [{
            "change_type": "ADDITION", "old_node": None, "new_node": node,
            "refactoring_kind": None,
        }],
        "change_groups": [],
        "has_semantic_changes": True, "is_style_only": False, "is_fallback": False,
    }


def test_corpus_parity_rejects_dropped_descendants():
    full = _diff(_node("module", children=[_node("count")]))
    truncated = _diff(_node("module"))
    assert _projection(full) != _projection(truncated)


def test_corpus_parity_rejects_corrupt_nested_spans_and_labels():
    full = _diff(_node("module", children=[_node("count", children=[_node("value", col=2)])]))
    changed_span = _diff(_node("module", children=[_node("count", children=[_node("value", col=5)])]))
    changed_label = _diff(_node("module", children=[_node("count", children=[_node("other", col=2)])]))
    assert _projection(full) != _projection(changed_span)
    assert _projection(full) != _projection(changed_label)


def test_corpus_parity_normalizes_absent_and_empty_child_lists():
    no_key = _diff(_node("value"))
    empty_key = _diff(_node("value", children=[]))
    assert _projection(no_key) == _projection(empty_key)


def test_corpus_parity_ignores_only_nonsemantic_ids():
    left = _diff(_node("value"))
    right = _diff(_node("value"))
    right["changes"][0]["new_node"]["id"] = "another-id"
    assert _projection(left) == _projection(right)
