"""Regression guards for semantic subtree parity in the 0.0.2 release gate."""
from __future__ import annotations

import runpy
from pathlib import Path

import pytest


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


def test_vbnet_declaration_extent_requires_closing_source_line():
    checker = runpy.run_path(str(SCRIPT))["check_vbnet_declaration_extent"]
    source = (Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
              / "vbnet" / "playground.vb").read_text(encoding="utf-8")
    case = {"id": "corpus-vbnet-addition", "new": source, "old": ""}
    declaration = _node("Multiply", col=4)
    declaration["node_type"] = "function_statement"
    declaration["position"] = {
        "start_line": 9, "start_col": 4, "end_line": 10, "end_col": 60,
    }
    with pytest.raises(AssertionError, match="VB.NET Multiply full declaration span"):
        checker(case, _diff(declaration))

    declaration["position"] = {
        "start_line": 9, "start_col": 4, "end_line": 11, "end_col": 16,
    }
    checker(case, _diff(declaration))


def test_vbnet_declaration_extent_rejects_silently_missing_declaration():
    checker = runpy.run_path(str(SCRIPT))["check_vbnet_declaration_extent"]
    source = (Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
              / "vbnet" / "playground.vb").read_text(encoding="utf-8")
    case = {"id": "corpus-vbnet-addition", "new": source, "old": ""}
    with pytest.raises(AssertionError, match="VB.NET Multiply full declaration span"):
        checker(case, _diff(_node("unrelated")))


def test_corpus_addition_rejects_equal_but_fabricated_native_python_content():
    """Two routes agreeing on invented labels must fail source-judged acceptance."""
    checker = runpy.run_path(str(SCRIPT))["check_visible_corpus_tokens"]
    case = {
        "id": "corpus-python-addition",
        "old": "",
        "new": "def greet(name): return 'Hello'",
        "required_visible_tokens": ["greet", "name", "Hello"],
    }
    fabricated = _diff(_node("NOT IN SOURCE"))
    with pytest.raises(AssertionError, match="missing source-ratcheted semantic tokens"):
        checker(case, fabricated)
    # Presence must be inside the exposed new-tree descendants, not merely
    # in the input source or the other route's result.
    good = _diff(_node("module", children=[
        _node("greet", children=[_node("name"), _node("Hello")]),
    ]))
    checker(case, good)
    dropped_child = _diff(_node("module", children=[_node("greet")]))
    with pytest.raises(AssertionError, match="missing source-ratcheted semantic tokens"):
        checker(case, dropped_child)


def test_manifest_visible_tokens_are_carried_into_every_addition_case():
    harness = runpy.run_path(str(SCRIPT))
    cases = harness["full_corpus_cases"]()
    additions = [case for case in cases if case["id"].endswith("-addition")]
    assert len(additions) == 74
    for case in additions:
        manifest = (Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
                    / case["corpus_language"] / "playground.expect.json")
        expected = __import__("json").loads(manifest.read_text(encoding="utf-8"))
        assert case["required_visible_tokens"] == expected["visible_tokens"], case["id"]
