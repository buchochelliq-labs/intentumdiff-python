"""
Regression expectations for the playground's Python annotation example.

These tests describe the review-level behavior we want from the engine.  Adding
type annotations should refine existing functions and declarations, not make
stable code appear as unrelated additions/deletions.
"""

from __future__ import annotations

from collections.abc import Iterable

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import (
    Change,
    ChangeType,
    RefactoringKind,
    SemanticDiff,
    SemanticNode,
)


OLD_PLAYGROUND_EXAMPLE = """\
def greet(name):
    print("Hello, " + name)

def add(a, b):
    return a + b

class Counter:
    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1
"""


NEW_PLAYGROUND_EXAMPLE = """\
def greet(name: str) -> None:
    print(f"Hello, {name}")

def add(x: int, y: int) -> int:
    return x + y

class Counter:
    def __init__(self) -> None:
        self.count: int = 0

    def increment(self) -> None:
        self.count += 1
"""


def _diff(old: str, new: str) -> SemanticDiff:
    return SemanticDiffer().diff_strings(
        old,
        new,
        filename="code.py",
        language_hint="python",
    )


def _walk(node: SemanticNode | None) -> Iterable[SemanticNode]:
    if node is None:
        return
    yield node
    yield from node.descendants()


def _labels(node: SemanticNode | None) -> list[str]:
    return [item.label for item in _walk(node) if item.label]


def _side_labels(change: Change, side: str) -> list[str]:
    if side == "old":
        return _labels(change.old_node)
    if side == "new":
        return _labels(change.new_node)
    raise ValueError(f"unknown side: {side}")


def _all_labels(change: Change) -> list[str]:
    return [*_side_labels(change, "old"), *_side_labels(change, "new")]


def _mentions(change: Change, side: str, token: str) -> bool:
    return any(_label_matches(label, token) for label in _side_labels(change, side))


def _label_matches(label: str, token: str) -> bool:
    if label == token:
        return True
    # Single-letter identifiers such as a/b/x/y must match exactly.
    return len(token) > 1 and token in label


def _kind(change: Change) -> str | None:
    if change.refactoring_kind is None:
        return None
    if isinstance(change.refactoring_kind, RefactoringKind):
        return change.refactoring_kind.value
    return str(change.refactoring_kind)


def _summary(change: Change) -> str:
    old = "/".join(_side_labels(change, "old")[:5])
    new = "/".join(_side_labels(change, "new")[:5])
    return (
        f"{change.change_type} {_kind(change)} "
        f"old=[{old}] new=[{new}] {change.description}"
    )


def _summaries(changes: Iterable[Change]) -> str:
    return "\n".join(f"  - {_summary(change)}" for change in changes)


def _assert_structured(diff: SemanticDiff) -> None:
    assert not diff.is_fallback, f"unexpected fallback parse: {diff.parse_errors}"
    assert not diff.parse_errors
    assert diff.has_semantic_changes


def _assert_has_signature_change(diff: SemanticDiff, symbol: str) -> Change:
    matches = [
        change
        for change in diff.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind == RefactoringKind.CHANGE_SIGNATURE
        and _mentions(change, "old", symbol)
        and _mentions(change, "new", symbol)
    ]
    assert matches, (
        f"missing CHANGE_SIGNATURE for {symbol!r}; observed:\n"
        f"{_summaries(diff.changes)}"
    )
    return matches[0]


def _assert_has_rename(diff: SemanticDiff, old_label: str, new_label: str) -> None:
    rename_kinds = {
        RefactoringKind.RENAME_SYMBOL,
        RefactoringKind.RENAME_VARIABLE,
        RefactoringKind.RENAME_METHOD,
        RefactoringKind.RENAME_CLASS,
    }
    matches = [
        change
        for change in diff.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind in rename_kinds
        and _mentions(change, "old", old_label)
        and _mentions(change, "new", new_label)
    ]
    assert matches, (
        f"missing rename {old_label!r} -> {new_label!r}; observed:\n"
        f"{_summaries(diff.changes)}"
    )


def _assert_no_add_delete_for_labels(diff: SemanticDiff, labels: set[str]) -> None:
    offenders = [
        change
        for change in diff.changes
        if change.change_type in {ChangeType.ADDITION, ChangeType.DELETION}
        and any(_label_matches(label, token) for label in _all_labels(change) for token in labels)
    ]
    assert not offenders, (
        "stable code should not surface as standalone ADDITION/DELETION events:\n"
        f"{_summaries(offenders)}\n"
        "full change list:\n"
        f"{_summaries(diff.changes)}"
    )


def _assert_no_moves_or_reorders(diff: SemanticDiff) -> None:
    offenders = [
        change
        for change in diff.changes
        if change.change_type in {ChangeType.MOVE, ChangeType.REORDER}
    ]
    assert not offenders, (
        "annotation-only structure edits should not create MOVE/REORDER noise:\n"
        f"{_summaries(offenders)}"
    )


def test_method_return_annotation_refines_existing_method() -> None:
    old = """\
class Counter:
    def increment(self):
        self.count += 1
"""
    new = """\
class Counter:
    def increment(self) -> None:
        self.count += 1
"""

    diff = _diff(old, new)

    _assert_structured(diff)
    signature = _assert_has_signature_change(diff, "increment")
    assert _mentions(signature, "new", "None")
    _assert_no_add_delete_for_labels(diff, {"Counter", "increment", "self", "count", "1"})
    _assert_no_moves_or_reorders(diff)


def test_playground_annotation_example_reports_review_level_changes() -> None:
    diff = _diff(OLD_PLAYGROUND_EXAMPLE, NEW_PLAYGROUND_EXAMPLE)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)

    for symbol in ("greet", "add", "__init__", "increment"):
        _assert_has_signature_change(diff, symbol)

    _assert_has_rename(diff, "a", "x")
    _assert_has_rename(diff, "b", "y")

    assert any(
        change.change_type == ChangeType.MODIFICATION
        and _mentions(change, "old", "Hello,")
        and _mentions(change, "new", "Hello, {name}")
        for change in diff.changes
    ), f"missing string-concat to f-string modification; observed:\n{_summaries(diff.changes)}"

    _assert_no_add_delete_for_labels(
        diff,
        {
            "greet",
            "add",
            "Counter",
            "__init__",
            "increment",
            "print",
            "name",
            "a",
            "b",
            "x",
            "y",
            "self",
            "count",
            "0",
            "1",
        },
    )
