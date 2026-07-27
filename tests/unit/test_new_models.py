"""
tests/unit/test_new_models.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for the cross-file analysis models added in v2:
``SymbolDefinition``, ``CrossFileChange``, ``CommitDiff``,
and the new ``ChangeType`` values.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intentdiff.core.models import (
    ChangeType,
    CommitDiff,
    CrossFileChange,
    NodePosition,
    SemanticDiff,
    SymbolDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos() -> NodePosition:
    return NodePosition(start_line=0, start_col=0, end_line=5, end_col=0)


def _symbol(
    qualified_name: str = "my_func",
    file: str = "a.py",
    node_type: str = "function_definition",
    node_id: str = "n1",
    language: str = "python",
) -> SymbolDefinition:
    return SymbolDefinition(
        qualified_name=qualified_name,
        file=file,
        node_type=node_type,
        node_id=node_id,
        start_line=0,
        start_col=0,
        end_line=5,
        end_col=0,
        language=language,
    )


def _cross_change(
    change_type: ChangeType = ChangeType.MOVE_TO_MODULE,
    symbol_name: str = "helper",
    old_file: str = "a.py",
    new_file: str = "b.py",
) -> CrossFileChange:
    return CrossFileChange(
        change_type=change_type,
        symbol_name=symbol_name,
        old_file=old_file,
        new_file=new_file,
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# ChangeType
# ---------------------------------------------------------------------------


class TestChangeType:
    def test_move_to_module_value(self):
        assert ChangeType.MOVE_TO_MODULE.value == "MOVE_TO_MODULE"

    def test_cross_file_rename_value(self):
        assert ChangeType.CROSS_FILE_RENAME.value == "CROSS_FILE_RENAME"

    def test_split_module_value(self):
        assert ChangeType.SPLIT_MODULE.value == "SPLIT_MODULE"

    def test_existing_change_types_unchanged(self):
        """Adding new values must not break existing ChangeType members."""
        assert ChangeType.ADDITION.value == "ADDITION"
        assert ChangeType.DELETION.value == "DELETION"
        assert ChangeType.MODIFICATION.value == "MODIFICATION"
        assert ChangeType.REFACTORING.value == "REFACTORING"


# ---------------------------------------------------------------------------
# SymbolDefinition
# ---------------------------------------------------------------------------


class TestSymbolDefinition:
    def test_basic_construction(self):
        sym = _symbol()
        assert sym.qualified_name == "my_func"
        assert sym.file == "a.py"
        assert sym.language == "python"

    def test_is_frozen(self):
        sym = _symbol()
        with pytest.raises(Exception):  # Pydantic v2 raises ValidationError on setattr
            setattr(sym, "qualified_name", "mutated")

    def test_empty_qualified_name_raises(self):
        with pytest.raises(ValidationError):
            SymbolDefinition(
                qualified_name="",
                file="a.py",
                node_type="function_definition",
                node_id="n1",
                start_line=0,
                start_col=0,
                end_line=0,
                end_col=0,
            )

    def test_empty_file_raises(self):
        with pytest.raises(ValidationError):
            SymbolDefinition(
                qualified_name="foo",
                file="",
                node_type="function_definition",
                node_id="n1",
                start_line=0,
                start_col=0,
                end_line=0,
                end_col=0,
            )

    def test_language_defaults_to_empty_string(self):
        sym = SymbolDefinition(
            qualified_name="foo",
            file="a.py",
            node_type="function_definition",
            node_id="n1",
            start_line=0,
            start_col=0,
            end_line=0,
            end_col=0,
        )
        assert sym.language == ""

    def test_serialisation_roundtrip(self):
        sym = _symbol("MyClass.method", "models.py", "method_definition", "m1", "java")
        restored = SymbolDefinition.model_validate(sym.model_dump())
        assert restored == sym


# ---------------------------------------------------------------------------
# CrossFileChange
# ---------------------------------------------------------------------------


class TestCrossFileChange:
    def test_move_to_module(self):
        c = _cross_change(ChangeType.MOVE_TO_MODULE)
        assert c.change_type == ChangeType.MOVE_TO_MODULE
        assert c.old_file == "a.py"
        assert c.new_file == "b.py"
        assert c.confidence == 1.0

    def test_cross_file_rename(self):
        c = _cross_change(ChangeType.CROSS_FILE_RENAME)
        assert c.change_type == ChangeType.CROSS_FILE_RENAME

    def test_optional_node_ids_default_none(self):
        c = _cross_change()
        assert c.old_node_id is None
        assert c.new_node_id is None

    def test_node_ids_can_be_set(self):
        c = CrossFileChange(
            change_type=ChangeType.MOVE_TO_MODULE,
            symbol_name="foo",
            old_file="a.py",
            new_file="b.py",
            old_node_id="old-1",
            new_node_id="new-2",
        )
        assert c.old_node_id == "old-1"
        assert c.new_node_id == "new-2"

    def test_confidence_must_be_0_to_1(self):
        with pytest.raises(ValidationError):
            CrossFileChange(
                change_type=ChangeType.MOVE_TO_MODULE,
                symbol_name="foo",
                old_file="a.py",
                new_file="b.py",
                confidence=1.5,
            )

    def test_confidence_0_is_valid(self):
        c = CrossFileChange(
            change_type=ChangeType.MOVE_TO_MODULE,
            symbol_name="foo",
            old_file="a.py",
            new_file="b.py",
            confidence=0.0,
        )
        assert c.confidence == 0.0

    def test_description_defaults_to_empty(self):
        c = _cross_change()
        assert c.description == ""

    def test_serialisation_roundtrip(self):
        c = CrossFileChange(
            change_type=ChangeType.CROSS_FILE_RENAME,
            symbol_name="old_fn",
            old_file="x.py",
            new_file="y.py",
            old_node_id="a",
            new_node_id="b",
            old_position=NodePosition(
                start_line=1,
                start_col=2,
                end_line=3,
                end_col=4,
            ),
            new_position=NodePosition(
                start_line=5,
                start_col=6,
                end_line=7,
                end_col=8,
            ),
            old_language="python",
            new_language="python",
            node_type="function_definition",
            symbol_kind="function",
            confidence=0.8,
            description="renamed",
        )
        restored = CrossFileChange.model_validate(c.model_dump())
        assert restored == c
        assert restored.new_position is not None
        assert restored.new_position.start_line == 5
        assert restored.node_type == "function_definition"
        assert restored.symbol_kind == "function"


# ---------------------------------------------------------------------------
# CommitDiff
# ---------------------------------------------------------------------------


class TestCommitDiff:
    def test_basic_construction(self):
        cd = CommitDiff(old_ref="HEAD~1", new_ref="HEAD")
        assert cd.old_ref == "HEAD~1"
        assert cd.new_ref == "HEAD"
        assert cd.file_diffs == []
        assert cd.cross_file_changes == []
        assert cd.guardrail_violations == []
        assert cd.parse_errors == []

    def test_with_file_diffs(self):
        diff = SemanticDiff.style_only("a.py", "b.py", "python")
        cd = CommitDiff(old_ref="abc123", new_ref="def456", file_diffs=[diff])
        assert len(cd.file_diffs) == 1
        assert cd.file_diffs[0] is diff

    def test_with_cross_file_changes(self):
        c = _cross_change()
        cd = CommitDiff(old_ref="a", new_ref="b", cross_file_changes=[c])
        assert len(cd.cross_file_changes) == 1

    def test_with_parse_errors(self):
        cd = CommitDiff(old_ref="a", new_ref="b", parse_errors=["broken.py: parse failed"])
        assert len(cd.parse_errors) == 1
        assert "broken.py" in cd.parse_errors[0]

    def test_is_frozen(self):
        cd = CommitDiff(old_ref="a", new_ref="b")
        with pytest.raises(Exception):  # Pydantic v2 raises ValidationError on setattr
            setattr(cd, "old_ref", "mutated")

    def test_serialisation_roundtrip(self):
        diff = SemanticDiff.style_only("a.py", "b.py", "python")
        cc = _cross_change(ChangeType.MOVE_TO_MODULE)
        cd = CommitDiff(
            old_ref="HEAD~1",
            new_ref="HEAD",
            file_diffs=[diff],
            cross_file_changes=[cc],
            parse_errors=["warn.py: skipped"],
        )
        restored = CommitDiff.model_validate(cd.model_dump())
        assert restored.old_ref == cd.old_ref
        assert len(restored.file_diffs) == 1
        assert len(restored.cross_file_changes) == 1
        assert restored.parse_errors == ["warn.py: skipped"]
