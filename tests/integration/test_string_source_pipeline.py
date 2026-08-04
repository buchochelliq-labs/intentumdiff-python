"""
tests/integration/test_string_source_pipeline.py

Integration tests that exercise the full pipeline using StringSource
(no git, no Wasm builds required).  These tests skip if the tree-sitter
grammars are not installed.
"""

from __future__ import annotations

import pytest

ts = pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from intentumdiff.core.models import ChangeType, DiffConfig, SemanticDiff
from intentumdiff.sources.string_source import StringSource


pytestmark = pytest.mark.integration


def _diff(old: str, new: str, lang: str = "python") -> SemanticDiff:
    """Run the full diff pipeline end-to-end (tree-sitter + Wasm plugin)."""
    from intentumdiff.differ import SemanticDiffer

    # Use a generous fuel budget: the python_parser.wasm processes a full CST
    # JSON tree which can consume >10 M instructions for even small files.
    config = DiffConfig(detect_refactorings=True, plugin_fuel=200_000_000)
    differ = SemanticDiffer(config=config)
    src = StringSource(old, new, "test.py", language_hint=lang)
    return differ.diff(src)


class TestStyleOnlyShortcut:
    def test_identical_source_is_style_only(self):
        code = "def foo():\n    pass\n"
        result = _diff(code, code)
        assert result.is_style_only
        assert result.changes == []

    def test_comment_added_is_style_only(self, py_old, py_new_style_only):
        result = _diff(py_old, py_new_style_only)
        # Comments are trivia — should still be style-only or zero semantic changes
        assert result.is_style_only or not result.has_semantic_changes


class TestSemanticChanges:
    def test_rename_detected(self, py_old, py_new_rename):
        result = _diff(py_old, py_new_rename)
        assert result.has_semantic_changes
        types = {c.change_type for c in result.changes}
        # A rename may manifest as MODIFICATION/REFACTORING on the identifier
        # node, or as ADDITION + DELETION + MOVE when the engine matches the
        # old and new function bodies by structural hash.
        assert (
            ChangeType.MODIFICATION in types
            or ChangeType.REFACTORING in types
            or ChangeType.MOVE in types
        )

    def test_sql_column_added(self, sql_old, sql_new_column_add):
        result = _diff(sql_old, sql_new_column_add, lang="sql")
        assert result.has_semantic_changes
