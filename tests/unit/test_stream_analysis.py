"""Unit tests for ChangeStreamEvent, ChangeStreamPhase, and _changes_to_stream_events."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, node_type: str = "identifier"):
    from intentdiff.core.models import NodePosition, SemanticNode

    return SemanticNode(
        id=nid,
        node_type=node_type,
        label=nid,
        position=NodePosition(start_line=0, start_col=0, end_line=0, end_col=0),
        structural_hash=nid,
    )


def _add(nid: str):
    from intentdiff.core.models import Change, ChangeType

    return Change(change_type=ChangeType.ADDITION, new_node=_node(nid))


def _del(nid: str):
    from intentdiff.core.models import Change, ChangeType

    return Change(change_type=ChangeType.DELETION, old_node=_node(nid))


def _mod(old_id: str, new_id: str):
    from intentdiff.core.models import Change, ChangeType

    return Change(
        change_type=ChangeType.MODIFICATION,
        old_node=_node(old_id),
        new_node=_node(new_id),
    )


def _refactor(old_id: str, new_id: str):
    from intentdiff.core.models import Change, ChangeType, RefactoringKind

    return Change(
        change_type=ChangeType.REFACTORING,
        old_node=_node(old_id),
        new_node=_node(new_id),
        refactoring_kind=RefactoringKind.RENAME_SYMBOL,
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestChangeStreamPhase:
    def test_values_are_ordered(self) -> None:
        from intentdiff.core.models import ChangeStreamPhase

        assert ChangeStreamPhase.STRUCTURAL < ChangeStreamPhase.REFINED < ChangeStreamPhase.FINAL

    def test_int_enum(self) -> None:
        from intentdiff.core.models import ChangeStreamPhase

        assert ChangeStreamPhase.STRUCTURAL == 1
        assert ChangeStreamPhase.REFINED == 2
        assert ChangeStreamPhase.FINAL == 3


class TestChangeStreamEvent:
    def test_add_event(self) -> None:
        from intentdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

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
        from intentdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        change = _refactor("old1", "new1")
        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.REFINED,
            action="revise",
            replaced_ids=["old1", "new1"],
            change=change,
        )
        assert event.action == "revise"
        assert event.replaced_ids == ["old1", "new1"]

    def test_remove_event(self) -> None:
        from intentdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.REFINED,
            action="remove",
            replaced_ids=["gone1"],
        )
        assert event.change is None
        assert event.replaced_ids == ["gone1"]

    def test_invalid_action_rejected(self) -> None:
        from pydantic import ValidationError

        from intentdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        with pytest.raises(ValidationError):
            ChangeStreamEvent(phase=ChangeStreamPhase.FINAL, action="invalid")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        from intentdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.STRUCTURAL, action="add", change=_add("x")
        )
        with pytest.raises((ValidationError, TypeError)):
            event.action = "remove"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _changes_to_stream_events
# ---------------------------------------------------------------------------


class TestChangesToStreamEvents:
    def _run(self, before, after, phase=None):
        from intentdiff.core.models import ChangeStreamPhase
        from intentdiff.differ import _changes_to_stream_events

        p = phase or ChangeStreamPhase.REFINED
        return list(_changes_to_stream_events(before, after, p))

    def test_empty_before_and_after(self) -> None:
        assert self._run([], []) == []

    def test_unchanged_changes_emit_nothing(self) -> None:
        """Changes that survive unchanged (same Python object) produce no events."""
        c1 = _add("n1")
        c2 = _del("n2")
        events = self._run([c1, c2], [c1, c2])
        assert events == []

    def test_new_change_emits_add(self) -> None:
        c1 = _add("n1")
        c2 = _add("n2")  # brand new — no predecessor
        events = self._run([c1], [c1, c2])
        assert len(events) == 1
        assert events[0].action == "add"
        assert events[0].change is c2
        assert events[0].replaced_ids == []

    def test_removed_change_emits_remove(self) -> None:
        c1 = _del("n1")
        events = self._run([c1], [])
        assert len(events) == 1
        e = events[0]
        assert e.action == "remove"
        assert e.change is None
        assert "n1" in e.replaced_ids

    def test_refactoring_replaces_deletion_and_addition(self) -> None:
        """DELETION + ADDITION → REFACTORING emits action='revise' with both node IDs."""
        d = _del("fn_old")
        a = _add("fn_new")
        r = _refactor("fn_old", "fn_new")  # new Change, references same node IDs
        events = self._run([d, a], [r])
        assert len(events) == 1
        e = events[0]
        assert e.action == "revise"
        assert e.change is r
        assert set(e.replaced_ids) == {"fn_old", "fn_new"}

    def test_refactoring_replaces_modification(self) -> None:
        """MODIFICATION → REFACTORING(RENAME) replaces both node IDs."""
        m = _mod("old_name", "new_name")
        r = _refactor("old_name", "new_name")
        events = self._run([m], [r])
        assert len(events) == 1
        e = events[0]
        assert e.action == "revise"
        assert set(e.replaced_ids) == {"old_name", "new_name"}

    def test_consumed_but_unreferenced_emits_remove(self) -> None:
        """A consumed change whose node IDs are NOT referenced by any new change → 'remove'."""
        c1 = _del("x1")  # consumed but not referenced by any new change
        c2 = _add("new_stuff")  # new, no relation to c1
        # Simulate: c1 is consumed (missing from after), c2 is added
        events = self._run([c1], [c2])
        actions = {e.action for e in events}
        assert "remove" in actions
        assert "add" in actions

    def test_phase_is_propagated(self) -> None:
        from intentdiff.core.models import ChangeStreamPhase

        c = _add("n1")
        events = self._run([], [c], phase=ChangeStreamPhase.FINAL)
        assert events[0].phase == ChangeStreamPhase.FINAL


# ---------------------------------------------------------------------------
# EditDelta model
# ---------------------------------------------------------------------------


class TestEditDelta:
    def test_basic_delta(self) -> None:
        from intentdiff.core.models import EditDelta

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

        from intentdiff.core.models import EditDelta

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

        from intentdiff.core.models import EditDelta

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
        from intentdiff.core.models import DiffConfig

        assert DiffConfig().stream_analysis is False

    def test_can_set_true(self) -> None:
        from intentdiff.core.models import DiffConfig

        cfg = DiffConfig(stream_analysis=True)
        assert cfg.stream_analysis is True
