from __future__ import annotations

from difflib import SequenceMatcher

from intentumdiff.core.models import NodePosition, SemanticDiff


def changed_lines(old: str, new: str) -> tuple[set[int], set[int]]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    old_changed: set[int] = set()
    new_changed: set[int] = set()
    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_changed.update(range(old_start, old_end))
        new_changed.update(range(new_start, new_end))
    return old_changed, new_changed


def assert_semantic_changes_overlap_textual_hunks(
    diff: SemanticDiff,
    old: str,
    new: str,
) -> None:
    old_changed, new_changed = changed_lines(old, new)
    for change in diff.changes:
        if change.old_node is not None and change.old_node.position is not None:
            old_lines = set(
                range(
                    change.old_node.position.start_line,
                    change.old_node.position.end_line + 1,
                )
            )
            assert old_lines & old_changed, change.description
        if change.new_node is not None and change.new_node.position is not None:
            new_lines = set(
                range(
                    change.new_node.position.start_line,
                    change.new_node.position.end_line + 1,
                )
            )
            assert new_lines & new_changed, change.description


def assert_no_identical_positioned_source_modifications(
    diff: SemanticDiff,
    old: str,
    new: str,
) -> None:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    for change in diff.changes:
        if (
            change.change_type.value != "MODIFICATION"
            or change.old_node is None
            or change.new_node is None
            or change.old_node.position is None
            or change.new_node.position is None
        ):
            continue
        old_text = _source_span(old_lines, change.old_node.position)
        new_text = _source_span(new_lines, change.new_node.position)
        if not old_text and not new_text:
            continue
        if change.old_node.label not in old_text or change.new_node.label not in new_text:
            continue
        assert old_text != new_text, change.description


def _source_span(lines: list[str], position: NodePosition) -> str:
    start_line = position.start_line
    end_line = position.end_line
    if start_line < 0 or end_line < start_line or start_line >= len(lines):
        return ""
    end_line = min(end_line, len(lines) - 1)
    if start_line == end_line:
        return lines[start_line][position.start_col : position.end_col]
    selected = lines[start_line : end_line + 1]
    selected[0] = selected[0][position.start_col :]
    selected[-1] = selected[-1][: position.end_col]
    return "\n".join(selected)
