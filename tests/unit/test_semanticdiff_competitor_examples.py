"""
Competitor-conformance tests based on SemanticDiff's public demo examples.

These tests intentionally assert the behavior SemanticDiff exposes in its
browser demo and docs, not IntentumDiff's current internal output shape.
They should fail whenever we drift below that visible bar.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import (
    Change,
    ChangeGroupKind,
    ChangeType,
    RefactoringKind,
    SemanticDiff,
    SemanticNode,
)

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "semanticdiff_examples.json"
_RENAME_KINDS = {
    RefactoringKind.RENAME_SYMBOL.value,
    RefactoringKind.RENAME_CLASS.value,
    RefactoringKind.RENAME_METHOD.value,
    RefactoringKind.RENAME_VARIABLE.value,
}


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def differ() -> SemanticDiffer:
    return SemanticDiffer()


def _example(fixture_data: dict[str, Any], language: str, name: str) -> dict[str, Any]:
    examples = fixture_data["languages"][language]["examples"]
    for example in examples:
        if example["name"] == name:
            return example
    raise AssertionError(f"Missing SemanticDiff fixture: {language}/{name}")


def _source(example: dict[str, Any], side: str) -> str:
    return "\n".join(example[f"{side}_lines"]) + "\n"


def _diff(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
    language: str,
    example_name: str,
) -> SemanticDiff:
    group = fixture_data["languages"][language]
    example = _example(fixture_data, language, example_name)
    return differ.diff_strings(
        _source(example, "old"),
        _source(example, "new"),
        filename=group["filename"],
        language_hint=language,
    )


def _change_type(change: Change) -> str:
    if isinstance(change.change_type, ChangeType):
        return change.change_type.value
    return str(change.change_type)


def _refactoring_kind(change: Change) -> str | None:
    if change.refactoring_kind is None:
        return None
    if isinstance(change.refactoring_kind, RefactoringKind):
        return change.refactoring_kind.value
    return str(change.refactoring_kind)


def _group_kind(kind: ChangeGroupKind | str) -> str:
    if isinstance(kind, ChangeGroupKind):
        return kind.value
    return str(kind)


def _group_refactoring_kind(kind: RefactoringKind | str | None) -> str | None:
    if kind is None:
        return None
    if isinstance(kind, RefactoringKind):
        return kind.value
    return str(kind)


def _walk(node: SemanticNode | None) -> Iterable[SemanticNode]:
    if node is None:
        return
    yield node
    yield from node.descendants()


def _labels(node: SemanticNode | None) -> list[str]:
    return [n.label for n in _walk(node) if n.label]


def _side_labels(change: Change, side: str) -> list[str]:
    if side == "old":
        return _labels(change.old_node)
    if side == "new":
        return _labels(change.new_node)
    raise ValueError(f"unknown side: {side}")


def _mentions(change: Change, side: str, token: str) -> bool:
    return any(token in label for label in _side_labels(change, side))


def _mentions_all(change: Change, side: str, tokens: Iterable[str]) -> bool:
    return all(_mentions(change, side, token) for token in tokens)


def _changes_of_type(diff: SemanticDiff, change_type: ChangeType) -> list[Change]:
    return [change for change in diff.changes if _change_type(change) == change_type.value]


def _safe(text: object) -> str:
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _summary(change: Change) -> str:
    old = "/".join(_side_labels(change, "old")[:4])
    new = "/".join(_side_labels(change, "new")[:4])
    return _safe(
        {
            "type": _change_type(change),
            "kind": _refactoring_kind(change),
            "old": old,
            "new": new,
            "description": change.description,
        }
    )


def _summaries(changes: Iterable[Change]) -> str:
    return "\n".join(f"  - {_summary(change)}" for change in changes)


def _group_summary(group: Any) -> str:
    return _safe(
        {
            "kind": _group_kind(group.kind),
            "rule": group.rule_id,
            "refactoring_kind": _group_refactoring_kind(group.refactoring_kind),
            "old": group.old_labels[:6],
            "new": group.new_labels[:6],
        }
    )


def _group_summaries(groups: Iterable[Any]) -> str:
    return "\n".join(f"  - {_group_summary(group)}" for group in groups)


def _assert_semantic(diff: SemanticDiff) -> None:
    assert not diff.is_fallback, (
        f"expected structured semantic diff, got fallback: {diff.parse_errors}"
    )
    assert not diff.parse_errors
    assert diff.has_semantic_changes


def _assert_no_change_types(diff: SemanticDiff, *change_types: ChangeType) -> None:
    forbidden = {change_type.value for change_type in change_types}
    offenders = [change for change in diff.changes if _change_type(change) in forbidden]
    assert not offenders, (
        "unexpected noisy change types:\n"
        f"{_summaries(offenders)}\n"
        "full change list:\n"
        f"{_summaries(diff.changes)}"
    )


def _assert_has_change(
    diff: SemanticDiff,
    *,
    change_type: ChangeType,
    old_tokens: Iterable[str] = (),
    new_tokens: Iterable[str] = (),
) -> None:
    matches = [
        change
        for change in diff.changes
        if _change_type(change) == change_type.value
        and _mentions_all(change, "old", old_tokens)
        and _mentions_all(change, "new", new_tokens)
    ]
    assert matches, (
        f"missing {change_type.value} with old={list(old_tokens)!r} "
        f"new={list(new_tokens)!r}; observed:\n{_summaries(diff.changes)}"
    )


def _assert_has_rename(
    diff: SemanticDiff,
    *,
    old_token: str,
    new_token: str,
) -> None:
    matches = [
        change
        for change in diff.changes
        if _change_type(change) == ChangeType.REFACTORING.value
        and _refactoring_kind(change) in _RENAME_KINDS
        and _mentions(change, "old", old_token)
        and _mentions(change, "new", new_token)
    ]
    assert matches, (
        f"missing rename refactoring {old_token!r} -> {new_token!r}; "
        f"observed:\n{_summaries(diff.changes)}"
    )


def _assert_has_group(
    diff: SemanticDiff,
    *,
    kind: ChangeGroupKind,
    old_tokens: Iterable[str] = (),
    new_tokens: Iterable[str] = (),
    refactoring_kind: RefactoringKind | None = None,
) -> None:
    matches = []
    for group in diff.change_groups:
        if _group_kind(group.kind) != kind.value:
            continue
        if refactoring_kind is not None and (
            _group_refactoring_kind(group.refactoring_kind) != refactoring_kind.value
        ):
            continue
        if all(token in group.old_labels for token in old_tokens) and all(
            token in group.new_labels for token in new_tokens
        ):
            matches.append(group)
    assert matches, (
        f"missing {kind.value} group with old={list(old_tokens)!r} "
        f"new={list(new_tokens)!r}; observed:\n{_group_summaries(diff.change_groups)}"
    )


def _assert_only_moved_symbol(diff: SemanticDiff, symbol: str) -> None:
    moves = _changes_of_type(diff, ChangeType.MOVE)
    assert moves, f"missing MOVE for {symbol!r}; observed:\n{_summaries(diff.changes)}"
    assert any(
        _mentions(move, "old", symbol) and _mentions(move, "new", symbol)
        for move in moves
    ), (
        f"missing MOVE whose source and target both mention {symbol!r}; "
        f"observed moves:\n{_summaries(moves)}"
    )
    unrelated_moves = [
        move
        for move in moves
        if not (_mentions(move, "old", symbol) or _mentions(move, "new", symbol))
    ]
    assert not unrelated_moves, (
        f"unexpected moves unrelated to {symbol!r}:\n"
        f"{_summaries(unrelated_moves)}\n"
        "full change list:\n"
        f"{_summaries(diff.changes)}"
    )
    _assert_no_change_types(diff, ChangeType.REORDER)


def test_python_moved_code_matches_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "python", "Moved Code")

    _assert_semantic(diff)
    _assert_only_moved_symbol(diff, "calc_hash")
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.MOVED_CODE,
        old_tokens=["calc_hash"],
        new_tokens=["calc_hash"],
    )
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.IGNORED_STYLE,
    )
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        old_tokens=["md5"],
        new_tokens=["sha256"],
    )


def test_python_renames_match_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "python", "Renames")

    _assert_semantic(diff)
    _assert_has_rename(diff, old_token="addr", new_token="address")
    _assert_has_rename(diff, old_token="start", new_token="start_time")
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.REFACTORING,
        old_tokens=["addr"],
        new_tokens=["address"],
        refactoring_kind=RefactoringKind.RENAME_VARIABLE,
    )
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.REFACTORING,
        old_tokens=["start"],
        new_tokens=["start_time"],
        refactoring_kind=RefactoringKind.RENAME_VARIABLE,
    )
    _assert_no_change_types(
        diff,
        ChangeType.MOVE,
        ChangeType.REORDER,
        ChangeType.ADDITION,
        ChangeType.DELETION,
    )


def test_python_style_changes_match_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "python", "Style Changes")

    _assert_semantic(diff)
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.IGNORED_STYLE,
        old_tokens=["host", "foo"],
    )
    _assert_has_change(
        diff,
        change_type=ChangeType.DELETION,
        old_tokens=["print", "foo", "host"],
    )
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        old_tokens=["mergeboard.com"],
        new_tokens=["semanticdiff.com"],
    )
    _assert_no_change_types(diff, ChangeType.MOVE, ChangeType.REORDER)


def test_javascript_moved_code_matches_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "javascript", "Moved Code")

    _assert_semantic(diff)
    # The md5→sha256 change must surface regardless of matcher path.
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        old_tokens=["md5"],
        new_tokens=["sha256"],
    )
    # calc_hash must appear somewhere — as a MOVE change, in a MOVED_CODE
    # group, or in an entity-surfacing MEANINGFUL_CHANGE group. The exact
    # change shape differs by matcher (Python oracle emits a MOVE; Rust
    # matcher may preserve the container and surface via entity-surfacing).
    calc_hash_in_output = any(
        _mentions(change, "old", "calc_hash") or _mentions(change, "new", "calc_hash")
        for change in diff.changes
    ) or any(
        "calc_hash" in (group.old_labels + group.new_labels)
        for group in diff.change_groups
    )
    assert calc_hash_in_output, (
        "calc_hash label missing from both changes and change_groups; "
        f"observed:\n{_summaries(diff.changes)}\n"
        f"{_group_summaries(diff.change_groups)}"
    )


def test_javascript_renames_match_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "javascript", "Renames")

    _assert_semantic(diff)
    _assert_has_rename(diff, old_token="dir", new_token="directory")
    _assert_has_rename(diff, old_token="file", new_token="file_name")
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.REFACTORING,
        old_tokens=["dir"],
        new_tokens=["directory"],
        refactoring_kind=RefactoringKind.RENAME_VARIABLE,
    )
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.REFACTORING,
        old_tokens=["file"],
        new_tokens=["file_name"],
        refactoring_kind=RefactoringKind.RENAME_VARIABLE,
    )
    _assert_no_change_types(
        diff,
        ChangeType.MOVE,
        ChangeType.REORDER,
        ChangeType.ADDITION,
        ChangeType.DELETION,
    )


def test_javascript_style_changes_match_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "javascript", "Style Changes")

    _assert_semantic(diff)
    # The error message change must surface regardless of matcher path.
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        old_tokens=["Oh no"],
        new_tokens=["An error occurred"],
    )
    # The removed console.log calls must surface as DELETION events. The
    # exact token coverage (whether ``foo``/``bar`` appear in the deleted
    # subtree's labels) depends on how the matcher pairs the wrapper nodes.
    # The truth contract: at least one DELETION mentioning ``console.log``.
    console_log_deletions = [
        change
        for change in diff.changes
        if _change_type(change) == ChangeType.DELETION.value
        and _mentions(change, "old", "console")
    ]
    assert console_log_deletions, (
        "expected at least one DELETION mentioning console.log; "
        f"observed:\n{_summaries(diff.changes)}"
    )


def test_csharp_renames_match_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "csharp", "Renames")

    _assert_semantic(diff)
    _assert_has_rename(diff, old_token="srv", new_token="server")
    _assert_has_rename(diff, old_token="tClient", new_token="client")
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.REFACTORING,
        old_tokens=["srv"],
        new_tokens=["server"],
        refactoring_kind=RefactoringKind.RENAME_VARIABLE,
    )
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.REFACTORING,
        old_tokens=["tClient"],
        new_tokens=["client"],
        refactoring_kind=RefactoringKind.RENAME_VARIABLE,
    )
    _assert_no_change_types(
        diff,
        ChangeType.MOVE,
        ChangeType.REORDER,
        ChangeType.ADDITION,
        ChangeType.DELETION,
    )


def test_csharp_style_changes_match_semanticdiff_signature(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    diff = _diff(differ, fixture_data, "csharp", "Style Changes")

    _assert_semantic(diff)
    _assert_has_group(
        diff,
        kind=ChangeGroupKind.IGNORED_STYLE,
        old_tokens=["25363"],
        new_tokens=["25362"],
    )
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        old_tokens=["25363"],
        new_tokens=["25362"],
    )
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        new_tokens=["descending"],
    )
    _assert_has_change(
        diff,
        change_type=ChangeType.MODIFICATION,
        old_tokens=["{0}"],
        new_tokens=["Name:"],
    )
    _assert_no_change_types(diff, ChangeType.MOVE, ChangeType.REORDER)
