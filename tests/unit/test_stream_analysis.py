"""DTO and Rust-backed stream API contract tests."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, node_type: str = "identifier"):
    from intentumdiff.core.models import NodePosition, SemanticNode

    return SemanticNode(
        id=nid,
        node_type=node_type,
        label=nid,
        position=NodePosition(start_line=0, start_col=0, end_line=0, end_col=0),
        structural_hash=nid,
    )


def _add(nid: str):
    from intentumdiff.core.models import Change, ChangeType

    return Change(change_type=ChangeType.ADDITION, new_node=_node(nid))



# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestChangeStreamPhase:
    def test_values_are_ordered(self) -> None:
        from intentumdiff.core.models import ChangeStreamPhase

        assert ChangeStreamPhase.STRUCTURAL < ChangeStreamPhase.REFINED < ChangeStreamPhase.FINAL

    def test_int_enum(self) -> None:
        from intentumdiff.core.models import ChangeStreamPhase

        assert ChangeStreamPhase.STRUCTURAL == 1
        assert ChangeStreamPhase.REFINED == 2
        assert ChangeStreamPhase.FINAL == 3


class TestChangeStreamEvent:
    def test_add_event(self) -> None:
        from intentumdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        change = _add("n1")
        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.STRUCTURAL,
            action="add",
            change=change,
        )
        assert event.action == "add"
        assert event.change is change
        assert event.replaced_ids == []

    def test_revise_event(self) -> None:
        from intentumdiff.core.models import (
            Change, ChangeType, ChangeStreamEvent, ChangeStreamPhase, RefactoringKind,
        )

        # DTO compatibility only: Python does not infer revisions or refactorings.
        change = Change(
            change_type=ChangeType.REFACTORING,
            old_node=_node("old1"),
            new_node=_node("new1"),
            refactoring_kind=RefactoringKind.RENAME_SYMBOL,
        )
        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.REFINED,
            action="revise",
            replaced_ids=["old1", "new1"],
            change=change,
        )
        assert event.action == "revise"
        assert event.replaced_ids == ["old1", "new1"]

    def test_remove_event(self) -> None:
        from intentumdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.REFINED,
            action="remove",
            replaced_ids=["gone1"],
        )
        assert event.change is None
        assert event.replaced_ids == ["gone1"]

    def test_invalid_action_rejected(self) -> None:
        from pydantic import ValidationError

        from intentumdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        with pytest.raises(ValidationError):
            ChangeStreamEvent(phase=ChangeStreamPhase.FINAL, action="invalid")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        from intentumdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.STRUCTURAL, action="add", change=_add("x")
        )
        with pytest.raises((ValidationError, TypeError)):
            event.action = "remove"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Public stream transport over Rust-finalized changes
# ---------------------------------------------------------------------------


class TestRustBackedStream:
    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("answer = 1\n", "answer = 2\n"),
            ("π = 1\n", "π = 2\n"),
        ],
    )
    def test_semantic_changes_emit_final_add_events(self, old: str, new: str) -> None:
        from intentumdiff import SemanticDiffer
        from intentumdiff.core.models import ChangeStreamPhase
        from intentumdiff.sources.string_source import StringSource

        differ = SemanticDiffer()
        source = StringSource(old, new, "example.py", language_hint="python")
        final = differ.diff_strings(old, new, "example.py", language_hint="python")
        events = list(differ.diff_stream_progressive(source))

        assert final.has_semantic_changes is True
        assert events
        assert len(events) == len(final.changes)
        assert [event.change.change_type for event in events] == [
            change.change_type for change in final.changes
        ]
        assert all(event.phase == ChangeStreamPhase.FINAL for event in events)
        assert all(event.action == "add" and not event.replaced_ids for event in events)
        assert all(event.change is not None for event in events)

    def test_style_only_changes_do_not_create_fake_stream_events(self) -> None:
        from intentumdiff import SemanticDiffer
        from intentumdiff.sources.string_source import StringSource

        old = "answer = 1\n"
        new = "# comment\nanswer = 1\n"
        differ = SemanticDiffer()
        final = differ.diff_strings(old, new, "example.py", language_hint="python")
        events = list(
            differ.diff_stream_progressive(
                StringSource(old, new, "example.py", language_hint="python")
            )
        )

        assert final.has_semantic_changes is False
        assert final.changes == []
        assert events == []


# ---------------------------------------------------------------------------
# EditDelta model
# ---------------------------------------------------------------------------


class TestEditDelta:
    def test_basic_delta(self) -> None:
        from intentumdiff.core.models import EditDelta

        delta = EditDelta(
            start_byte=10,
            old_end_byte=20,
            new_end_byte=25,
            start_point=(1, 4),
            old_end_point=(1, 14),
            new_end_point=(1, 19),
        )
        assert delta.start_byte == 10
        assert delta.start_point == (1, 4)

    def test_negative_byte_rejected(self) -> None:
        from pydantic import ValidationError

        from intentumdiff.core.models import EditDelta

        with pytest.raises(ValidationError):
            EditDelta(
                start_byte=-1,
                old_end_byte=0,
                new_end_byte=0,
                start_point=(0, 0),
                old_end_point=(0, 0),
                new_end_point=(0, 0),
            )

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        from intentumdiff.core.models import EditDelta

        d = EditDelta(
            start_byte=0,
            old_end_byte=0,
            new_end_byte=0,
            start_point=(0, 0),
            old_end_point=(0, 0),
            new_end_point=(0, 0),
        )
        with pytest.raises((ValidationError, TypeError)):
            d.start_byte = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DiffConfig.stream_analysis field
# ---------------------------------------------------------------------------


class TestDiffConfigStreamAnalysis:
    def test_default_is_false(self) -> None:
        from intentumdiff.core.models import DiffConfig

        assert DiffConfig().stream_analysis is False

    def test_can_set_true(self) -> None:
        from intentumdiff.core.models import DiffConfig

        cfg = DiffConfig(stream_analysis=True)
        assert cfg.stream_analysis is True
