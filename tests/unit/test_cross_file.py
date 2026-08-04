"""
tests/unit/test_cross_file.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for ``detect_cross_file_changes()``
(``intentumdiff.analysis.cross_file``).
"""

from __future__ import annotations

import pytest

from intentumdiff.analysis.cross_file import detect_cross_file_changes
from intentumdiff.core.index import SemanticIndex
from intentumdiff.core.models import ChangeType, NodePosition, SemanticNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(sl: int = 0, el: int = 5) -> NodePosition:
    return NodePosition(start_line=sl, start_col=0, end_line=el, end_col=0)


def _fn(id: str, label: str, start_line: int = 0) -> SemanticNode:
    return SemanticNode(
        id=id,
        node_type="function_definition",
        label=label,
        position=_pos(start_line, start_line + 5),
        structural_hash=f"h-{id}",
    )


def _cls(id: str, label: str, children: list[SemanticNode] | None = None) -> SemanticNode:
    return SemanticNode(
        id=id,
        node_type="class_definition",
        label=label,
        position=_pos(),
        structural_hash=f"h-{id}",
        children=children or [],
    )


def _built_index(*entries: tuple[str, str, SemanticNode]) -> SemanticIndex:
    """Create and build a SemanticIndex from (filename, language, tree) triples."""
    idx = SemanticIndex()
    for filename, language, tree in entries:
        idx.add_tree(filename, language, tree)
    idx.build()
    return idx


# ---------------------------------------------------------------------------
# MOVE_TO_MODULE tests
# ---------------------------------------------------------------------------


class TestMoveToModule:
    def test_detects_function_moved_between_files(self):
        old_idx = _built_index(("utils.py", "python", _fn("1", "helper")))
        new_idx = _built_index(("helpers.py", "python", _fn("2", "helper")))

        changes = detect_cross_file_changes(old_idx, new_idx)

        assert len(changes) == 1
        c = changes[0]
        assert c.change_type == ChangeType.MOVE_TO_MODULE
        assert c.symbol_name == "helper"
        assert c.old_file == "utils.py"
        assert c.new_file == "helpers.py"
        assert c.confidence == 1.0
        assert c.old_position == _pos()
        assert c.new_position == _pos()
        assert c.old_language == "python"
        assert c.new_language == "python"
        assert c.node_type == "function_definition"
        assert c.symbol_kind == "function"

    def test_no_move_when_file_unchanged(self):
        old_idx = _built_index(("utils.py", "python", _fn("1", "helper")))
        new_idx = _built_index(("utils.py", "python", _fn("1", "helper")))

        changes = detect_cross_file_changes(old_idx, new_idx)
        move_changes = [c for c in changes if c.change_type == ChangeType.MOVE_TO_MODULE]
        assert move_changes == []

    def test_no_move_when_symbol_removed(self):
        """Symbol removed entirely should not appear as MOVE_TO_MODULE."""
        old_idx = _built_index(("utils.py", "python", _fn("1", "gone")))
        new_idx = _built_index(("utils.py", "python", _fn("2", "other")))

        changes = detect_cross_file_changes(old_idx, new_idx)
        move_changes = [c for c in changes if c.change_type == ChangeType.MOVE_TO_MODULE]
        assert move_changes == []

    def test_multiple_moves_detected(self):
        old_idx = _built_index(
            ("a.py", "python", _fn("1", "foo")),
            ("a.py", "python", _fn("2", "bar")),
        )
        new_idx = _built_index(
            ("b.py", "python", _fn("3", "foo")),
            ("b.py", "python", _fn("4", "bar")),
        )
        changes = detect_cross_file_changes(old_idx, new_idx)
        move_names = {c.symbol_name for c in changes if c.change_type == ChangeType.MOVE_TO_MODULE}
        assert move_names == {"foo", "bar"}

    def test_move_description_mentions_files(self):
        old_idx = _built_index(("old.py", "python", _fn("1", "fn")))
        new_idx = _built_index(("new.py", "python", _fn("2", "fn")))
        changes = detect_cross_file_changes(old_idx, new_idx)
        assert "old.py" in changes[0].description
        assert "new.py" in changes[0].description

    def test_detects_file_split_when_symbols_move_to_multiple_files(self):
        old_idx = _built_index(
            ("file_a.py", "python", _fn("1", "alpha")),
            ("file_a.py", "python", _fn("2", "beta")),
        )
        new_idx = _built_index(
            ("file_b.py", "python", _fn("3", "alpha")),
            ("file_c.py", "python", _fn("4", "beta")),
        )

        changes = detect_cross_file_changes(old_idx, new_idx)
        split_changes = [c for c in changes if c.change_type == ChangeType.SPLIT_MODULE]

        assert len(split_changes) == 1
        assert split_changes[0].old_file == "file_a.py"
        assert split_changes[0].new_file == "file_b.py, file_c.py"
        assert split_changes[0].confidence == 0.9

    def test_detects_package_path_extract_refactor_depth(self):
        old_idx = _built_index(
            ("src/service.py", "python", _fn("old-parse", "parse_user", 12)),
            ("src/service.py", "python", _fn("old-email", "send_email", 40)),
        )
        new_idx = _built_index(
            ("src/service/parsing.py", "python", _fn("new-parse", "parse_user", 3)),
            ("src/service/email.py", "python", _fn("new-email", "send_email", 8)),
        )

        changes = detect_cross_file_changes(old_idx, new_idx)
        moves = [change for change in changes if change.change_type == ChangeType.MOVE_TO_MODULE]
        split = [change for change in changes if change.change_type == ChangeType.SPLIT_MODULE]

        assert {(change.symbol_name, change.old_file, change.new_file) for change in moves} == {
            ("parse_user", "src/service.py", "src/service/parsing.py"),
            ("send_email", "src/service.py", "src/service/email.py"),
        }
        assert len(split) == 1
        assert split[0].old_file == "src/service.py"
        assert split[0].new_file == "src/service/email.py, src/service/parsing.py"

    def test_file_split_ignores_ambiguous_duplicate_symbols(self):
        old_idx = _built_index(
            ("file_a.py", "python", _fn("1", "dup")),
            ("other.py", "python", _fn("2", "dup")),
            ("file_a.py", "python", _fn("3", "stable")),
        )
        new_idx = _built_index(
            ("file_b.py", "python", _fn("4", "dup")),
            ("file_c.py", "python", _fn("5", "stable")),
        )

        changes = detect_cross_file_changes(old_idx, new_idx)

        assert [c for c in changes if c.change_type == ChangeType.SPLIT_MODULE] == []


# ---------------------------------------------------------------------------
# CROSS_FILE_RENAME tests
# ---------------------------------------------------------------------------


class TestCrossFileRename:
    def test_detects_rename_in_same_file(self):
        old_idx = _built_index(("a.py", "python", _fn("1", "old_name")))
        new_idx = _built_index(("a.py", "python", _fn("2", "new_name")))

        changes = detect_cross_file_changes(old_idx, new_idx)
        rename_changes = [c for c in changes if c.change_type == ChangeType.CROSS_FILE_RENAME]
        assert len(rename_changes) == 1
        assert rename_changes[0].symbol_name == "old_name"
        assert rename_changes[0].confidence == 0.8

    def test_no_rename_when_two_removed_one_added(self):
        """Ambiguous case: two removed, one added → no rename (avoids false positives)."""
        old_idx = _built_index(
            ("a.py", "python", _fn("1", "alpha")),
            ("a.py", "python", _fn("2", "beta")),
        )
        new_idx = _built_index(("a.py", "python", _fn("3", "gamma")))

        changes = detect_cross_file_changes(old_idx, new_idx)
        rename_changes = [c for c in changes if c.change_type == ChangeType.CROSS_FILE_RENAME]
        assert rename_changes == []

    def test_no_rename_for_different_node_types(self):
        """A removed function and an added class in the same file are not a rename."""
        old_idx = _built_index(("a.py", "python", _fn("1", "MyThing")))
        new_idx = _built_index(("a.py", "python", _cls("2", "MyThing_v2")))

        changes = detect_cross_file_changes(old_idx, new_idx)
        rename_changes = [c for c in changes if c.change_type == ChangeType.CROSS_FILE_RENAME]
        assert rename_changes == []

    def test_rename_preserves_node_ids(self):
        old_idx = _built_index(("a.py", "python", _fn("id-old", "foo", 10)))
        new_idx = _built_index(("a.py", "python", _fn("id-new", "bar", 20)))

        changes = detect_cross_file_changes(old_idx, new_idx)
        rename_changes = [c for c in changes if c.change_type == ChangeType.CROSS_FILE_RENAME]
        rename = rename_changes[0]
        assert rename.old_node_id == "id-old"
        assert rename.new_node_id == "id-new"
        assert rename.old_position == _pos(10, 15)
        assert rename.new_position == _pos(20, 25)
        assert rename.old_language == "python"
        assert rename.new_language == "python"
        assert rename.node_type == "function_definition"
        assert rename.symbol_kind == "function"


# ---------------------------------------------------------------------------
# Empty index edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_both_empty_returns_empty(self):
        old_idx = SemanticIndex()
        old_idx.build()
        new_idx = SemanticIndex()
        new_idx.build()
        assert detect_cross_file_changes(old_idx, new_idx) == []

    def test_unchanged_symbols_produce_no_changes(self):
        tree = _fn("1", "stable_func")
        old_idx = _built_index(("a.py", "python", tree))
        new_idx = _built_index(("a.py", "python", tree))
        assert detect_cross_file_changes(old_idx, new_idx) == []
