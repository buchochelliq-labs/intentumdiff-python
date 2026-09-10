"""Core#44 / Python#45: semantic expectations, never Python-as-oracle parity.

Default configuration exercises native batch; diagnostics exercises the real
Wasm parser followed by Rust finalisation and Python's remaining postprocessing.
"""
import pytest

from intentumdiff import DiffConfig, SemanticDiffer


OLD = """import os
import sys

def calculate_total(items):
    total = 0
    for i in items:
        total += i.price * i.qty
    return min(total, 50)
"""
NEW = """import sys
import os

def _subtotal(i):
    return i.price * i.qty

def compute_order_total(items):
    total = 0
    for i in items:
        total += _subtotal(i)
    return min(total, 75)
"""


@pytest.fixture(params=[False, True], ids=["native-batch", "wasm-routed"])
def differ(request):
    instance = SemanticDiffer(DiffConfig(diagnostics=request.param))
    yield instance
    instance.close()


@pytest.mark.parametrize("old,new,before,after,literal_old,literal_new", [
    ("def calc(x):\n    return x + 1\n", "def compute(x):\n    return x + 2\n",
     "calc", "compute", "1", "2"),
    (OLD, NEW, "calculate_total", "compute_order_total", "50", "75"),
])
def test_rename_keeps_independent_behavior_change(differ, old, new, before, after, literal_old, literal_new):
    diff = differ.diff_strings(old, new, "example.py")
    assert diff.metadata["semantic_contract"] == (
        "rust_finalize_review_v1" if differ._config.diagnostics else "rust_finalized_v1"
    )
    assert not diff.is_fallback and not diff.is_style_only
    assert any(c.refactoring_kind == "RENAME_SYMBOL" and c.old_node and c.new_node
               and c.old_node.node_type == "function_definition"
               and (c.old_node.label, c.new_node.label) == (before, after)
               for c in diff.changes)
    assert any(c.change_type == "MODIFICATION" and c.old_node and c.new_node
               and (c.old_node.label, c.new_node.label) == (literal_old, literal_new)
               for c in diff.changes)
    assert all(c.change_type not in {"MOVE", "REORDER"} for c in diff.changes)
    assert not any(c.refactoring_kind == "RENAME_SYMBOL" and c.new_node
                   and c.new_node.label == "_subtotal" for c in diff.changes)
    for group in diff.change_groups:
        assert all(0 <= i < len(diff.changes) for i in group.raw_change_indices)
    literal_index = next(i for i, c in enumerate(diff.changes)
                         if c.change_type == "MODIFICATION" and c.new_node
                         and c.new_node.label == literal_new)
    assert any(g.kind == "MEANINGFUL_CHANGE" and literal_index in g.raw_change_indices
               for g in diff.change_groups)


@pytest.mark.parametrize("old,new", [
    ("def alpha(a): return a * 3\n", "def beta(b): return b - 9\n"),
    ("def old_one(): return 1\ndef keep(): return 0\n",
     "def keep(): return 0\ndef new_one(): return 2\n"),
    ("def alpha(x): return x + 1\ndef beta(x): return x + 1\n",
     "def gamma(x): return x + 2\ndef delta(x): return x + 2\n"),
    ("class A:\n    class Inner:\n        def calc(x): return x + 1\nclass B:\n    class Inner:\n        pass\n",
     "class A:\n    class Inner:\n        pass\nclass B:\n    class Inner:\n        def compute(x): return x + 2\n"),
])
def test_declines_unrelated_or_ambiguous_rename(differ, old, new):
    diff = differ.diff_strings(old, new, "example.py")
    assert not any(c.refactoring_kind == "RENAME_SYMBOL" for c in diff.changes)


@pytest.mark.parametrize("old_body,new_body", [
    ("    x = x + 1\n    x = x * 2\n", "    x = x * 2\n    x = x + 1\n"),
    ("    first(x)\n    second(x)\n", "    second(x)\n    first(x)\n"),
])
def test_rename_does_not_hide_executable_statement_reordering(differ, old_body, new_body):
    diff = differ.diff_strings("def calc(x):\n" + old_body + "    return x\n",
                               "def compute(x):\n" + new_body + "    return x\n", "example.py")
    assert any(c.refactoring_kind == "RENAME_SYMBOL" for c in diff.changes)
    assert any(c.change_type == "MODIFICATION" and "Reorder statement" in c.description
               for c in diff.changes)
    assert not any(g.rule_id == "python.formatting.call_wrapping_equivalence"
                   for g in diff.change_groups)


@pytest.mark.parametrize("before,after,equivalent", [
    ("1", "0x1", True), ("1_000", "1000", True),
    ("50", "75", False), ("9007199254740992", "9007199254740993", False),
])
def test_overlapping_literal_evaluators_against_exact_integer_expectations(before, after, equivalent):
    from intentumdiff.analysis.invariances import apply_invariances as python_apply
    from intentumdiff.rust_core import apply_invariances as rust_apply
    from intentumdiff.core.models import Change, NodePosition, SemanticNode

    def node(value):
        return SemanticNode(id="literal", node_type="integer", label=value,
                            position=NodePosition(start_line=0, start_col=0, end_line=0, end_col=len(value)),
                            structural_hash=value)

    old, new = node(before), node(after)
    change = Change(change_type="MODIFICATION", old_node=old, new_node=new,
                    confidence=1, description="Integer edit")
    for apply in (python_apply, rust_apply):
        result = apply([change], old_tree=old, new_tree=new,
                       old_source=before, new_source=after, language="python")
        assert bool(result.changes) is not equivalent
        assert bool(result.change_groups) is equivalent


@pytest.mark.parametrize("extension,language,parameter", [
    ("js", "javascript", "x"), ("ts", "typescript", "x: number"),
])
def test_callable_continuity_with_js_ts_wasm_parser(extension, language, parameter):
    differ = SemanticDiffer()
    try:
        diff = differ.diff_strings(
            f"function calc({parameter}) {{ return x + 1; }}",
            f"function compute({parameter}) {{ return x + 2; }}",
            f"example.{extension}", language_hint=language,
        )
    finally:
        differ.close()
    assert not diff.is_fallback
    assert any(c.refactoring_kind == "RENAME_SYMBOL" and c.old_node and c.new_node
               and c.old_node.node_type == "function_declaration"
               and (c.old_node.label, c.new_node.label) == ("calc", "compute")
               for c in diff.changes)
    assert any(c.change_type == "MODIFICATION" and c.old_node and c.new_node
               and (c.old_node.label, c.new_node.label) == ("1", "2")
               for c in diff.changes)
