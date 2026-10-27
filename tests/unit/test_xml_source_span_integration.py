"""Installed Python → Rust XML source-span acceptance for the 0.0.2 candidate.

The Rust core's synthetic markup text child must point at the actual source
bytes. An unquoted search for '>' or slicing a case-folded Unicode string
previously lost text changes or attached the wrong byte range.
"""

from __future__ import annotations

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import ChangeType


@pytest.mark.parametrize(
    "attribute",
    [
        'note="x>y"',
        "note='x>y'",
        'note="İ>y"',
    ],
)
def test_xml_quoted_delimiter_edit_is_meaningful_and_byte_grounded(
    attribute: str,
) -> None:
    old = f"<value {attribute}>before</value>"
    new = f"<value {attribute}>after!</value>"

    result = SemanticDiffer().diff_strings(
        old, new, filename="config.xml", language_hint="xml"
    )

    assert result.language == "xml"
    assert not result.is_fallback
    assert not result.is_style_only
    assert result.has_semantic_changes

    changes = [
        change for change in result.changes
        if change.change_type == ChangeType.MODIFICATION
        and change.old_node is not None
        and change.new_node is not None
        and change.old_node.label == "before"
        and change.new_node.label == "after!"
    ]
    assert len(changes) == 1, [
        (change.change_type, getattr(change.old_node, "label", None),
         getattr(change.new_node, "label", None))
        for change in result.changes
    ]

    for source, node, expected in (
        (old, changes[0].old_node, b"before"),
        (new, changes[0].new_node, b"after!"),
    ):
        position = node.position
        assert position.start_line == position.end_line == 0
        source_bytes = source.encode("utf-8")
        start = source_bytes.index(b">" + expected + b"<") + 1
        assert (position.start_col, position.end_col) == (
            start, start + len(expected)
        )
        assert source_bytes[position.start_col:position.end_col] == expected
