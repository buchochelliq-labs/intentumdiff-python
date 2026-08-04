from __future__ import annotations

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import ChangeType


def test_generic_tail_line_addition_is_compact() -> None:
    old = """MIT License

Copyright (c) 2026 IntentumDiff contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
SOFTWARE.
"""
    new = old + "\nA CHANGE"

    diff = SemanticDiffer().diff_strings(old, new, filename="LICENSE")

    assert diff.language == "generic"
    assert [change.change_type for change in diff.changes] == [
        ChangeType.ADDITION,
    ]
    assert diff.changes[0].new_node is not None
    assert diff.changes[0].new_node.label == "A CHANGE"
    assert diff.changes[0].new_node.position.start_line == 7


def test_generic_middle_line_addition_does_not_shift_rest_of_file() -> None:
    old = "alpha\nbravo\ncharlie\ndelta\n"
    new = "alpha\nbravo\ninserted\ncharlie\ndelta\n"

    diff = SemanticDiffer().diff_strings(old, new, filename="notes.txt")

    assert len(diff.changes) == 1
    assert diff.changes[0].change_type == ChangeType.ADDITION
    assert diff.changes[0].new_node is not None
    assert diff.changes[0].new_node.label == "inserted"
    assert diff.changes[0].new_node.position.start_line == 2


def test_generic_inline_insertion_is_one_line_modification_with_highlight() -> None:
    # A changed prose/text line is ONE line-level MODIFICATION; the inline highlight of the
    # inserted characters is preserved in ``text_diff`` (not a separate char-span change).
    old = "alpha bravo charlie\n"
    new = "alpha brave new bravo charlie\n"

    diff = SemanticDiffer().diff_strings(old, new, filename="notes.txt")

    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == ChangeType.MODIFICATION
    assert change.old_node is not None and change.new_node is not None
    assert change.old_node.node_type == "text_line"
    assert change.old_node.label == "alpha bravo charlie"
    assert change.new_node.label == "alpha brave new bravo charlie"
    assert change.text_diff == "alpha[+ brave new] bravo charlie"


def test_generic_inline_deletion_is_one_line_modification_with_highlight() -> None:
    old = "alpha brave new bravo charlie\n"
    new = "alpha bravo charlie\n"

    diff = SemanticDiffer().diff_strings(old, new, filename="notes.txt")

    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == ChangeType.MODIFICATION
    assert change.old_node is not None and change.new_node is not None
    assert change.old_node.label == "alpha brave new bravo charlie"
    assert change.new_node.label == "alpha bravo charlie"
    assert change.text_diff == "alpha[- brave new] bravo charlie"


def test_generic_inline_insert_and_delete_on_same_line_is_one_modification() -> None:
    old = "OUT OF OR IN CONNECTION WITH THE SOFTWARE\n"
    new = "OUT OF OR IN CONECTION COOL WITH THE SOFTWARE\n"

    diff = SemanticDiffer().diff_strings(old, new, filename="LICENSE")

    # One changed line = one MODIFICATION; the combined insert+delete highlight lives in text_diff.
    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == ChangeType.MODIFICATION
    assert change.old_node is not None and change.new_node is not None
    assert change.old_node.label == "OUT OF OR IN CONNECTION WITH THE SOFTWARE"
    assert change.new_node.label == "OUT OF OR IN CONECTION COOL WITH THE SOFTWARE"
    assert change.text_diff == "OUT OF OR IN CON[-N]ECTION[+ COOL] WITH THE SOFTWARE"


def test_markdown_heading_rename_is_one_section_change_not_duplicated() -> None:
    # A heading rename (body unchanged) is ONE section-level event, never a duplicated
    # heading-line + section pair. Since the tree-sitter markdown parser (issue #44)
    # took .md off the generic fallback, the shape is a first-class section RENAME on
    # real section nodes (labels carry the title without the `#` marker) instead of the
    # generic path's heuristic markdown_section modification.
    old = "# Old Title\n\nBody stays the same.\n"
    new = "# New Title\n\nBody stays the same.\n"

    diff = SemanticDiffer().diff_strings(old, new, filename="doc.md")

    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == ChangeType.REFACTORING
    assert change.old_node is not None and change.new_node is not None
    assert change.old_node.node_type == "section"
    assert change.old_node.label == "Old Title"
    assert change.new_node.label == "New Title"


def test_generic_whitespace_only_addition_is_ignored() -> None:
    old = "alpha\nbravo\n"
    new = "alpha\n   \nbravo\n"

    diff = SemanticDiffer().diff_strings(old, new, filename="notes.txt")

    assert diff.changes == []
