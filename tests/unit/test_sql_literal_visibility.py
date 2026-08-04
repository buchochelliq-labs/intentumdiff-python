"""Issue #16: SQL literal edits must never be false style-only."""

from __future__ import annotations

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import ChangeType

_OLD = "SELECT 1;\n"
_NEW = "SELECT 2;\n"


def test_sql_numeric_literal_edit_is_one_modification() -> None:
    diff = SemanticDiffer().diff_strings(_OLD, _NEW, filename="q.sql", language_hint="sql")
    assert not diff.is_style_only
    modifications = [c for c in diff.changes if c.change_type == ChangeType.MODIFICATION]
    assert len(modifications) == 1
    assert modifications[0].old_node is not None and modifications[0].old_node.label == "1"
    assert modifications[0].new_node is not None and modifications[0].new_node.label == "2"


def test_tsql_and_plsql_literal_edits_are_visible() -> None:
    for language in ("tsql", "plsql"):
        diff = SemanticDiffer().diff_strings(_OLD, _NEW, filename="q.sql", language_hint=language)
        assert not diff.is_style_only, language
        assert diff.changes, language
