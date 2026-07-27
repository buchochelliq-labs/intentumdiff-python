"""Dedicated .gitignore parser (issue #43).

Before this parser, a `.gitignore` edit fell through to the generic text parser, whose
token churn surfaced as a `NOISE_SUPPRESSED` "Suppressed N noisy changes" group plus an
"Ungrouped raw evidence" bucket — a one-line addition read as review noise. With pattern /
comment / negation nodes and blank lines dropped structurally, the same edit is a single
clean pattern change and nothing else.
"""

from __future__ import annotations

import pytest

from intentdiff import SemanticDiffer
from intentdiff.core.models import ChangeType


@pytest.fixture(scope="module")
def differ() -> SemanticDiffer:
    return SemanticDiffer()


def _diff(differ: SemanticDiffer, old: str, new: str, filename: str = ".gitignore"):
    return differ.diff_strings(old, new, filename=filename, language_hint=None)


def test_gitignore_is_detected_by_filename(differ: SemanticDiffer) -> None:
    diff = _diff(differ, "/target\n", "/target\n/dist\n")
    assert diff.language == "gitignore"
    assert not diff.is_fallback
    assert not diff.parse_errors


@pytest.mark.parametrize("filename", [".gitignore", ".dockerignore", "sub/.gitignore", "app.gitignore"])
def test_ignore_family_filenames_route_to_the_parser(differ: SemanticDiffer, filename: str) -> None:
    diff = _diff(differ, "a\n", "a\nb\n", filename=filename)
    assert diff.language == "gitignore"


def test_added_pattern_is_one_clean_change_with_no_noise_group(differ: SemanticDiffer) -> None:
    diff = _diff(
        differ,
        "# Build output\n/target\nnode_modules/\n",
        "# Build output\n/target\nnode_modules/\n/.intentdiff\n",
    )
    assert [c.change_type for c in diff.changes] == [ChangeType.ADDITION]
    added = diff.changes[0].new_node
    assert added is not None
    assert added.node_type == "pattern"
    assert added.label == "/.intentdiff"
    # The whole point of the parser: no generic-text token-churn suppression group.
    assert not any(
        group.rule_id == "presentation.generic_text_diff" for group in diff.change_groups
    )
    assert diff.has_semantic_changes


def test_blank_line_churn_is_invisible(differ: SemanticDiffer) -> None:
    # Adding blank lines between patterns must not surface as any change — this is what
    # generic-text tokenisation got wrong.
    diff = _diff(differ, "/a\n/b\n", "/a\n\n\n/b\n")
    assert diff.changes == []


def test_comment_edit_surfaces_as_a_comment_change(differ: SemanticDiffer) -> None:
    diff = _diff(differ, "# old section\n/a\n", "# new section\n/a\n")
    kinds = {(c.change_type, (c.new_node or c.old_node).node_type) for c in diff.changes}
    assert any(node_type == "comment" for _, node_type in kinds)


def test_negation_is_distinct_from_a_plain_pattern(differ: SemanticDiffer) -> None:
    diff = _diff(differ, "build/\n", "build/\n!build/keep.txt\n")
    assert [c.change_type for c in diff.changes] == [ChangeType.ADDITION]
    added = diff.changes[0].new_node
    assert added is not None
    assert added.node_type == "negated_pattern"
    assert added.label == "!build/keep.txt"


def test_engine_emits_human_intent_descriptions(differ: SemanticDiffer) -> None:
    # The engine (Rust finalize path) owns the intent wording so every frontend — review
    # tree, CodeLens, CLI, release notes — reads it from Change.description (issue #58).
    add = _diff(differ, "/target\n", "/target\n/.intentdiff\n").changes[0]
    assert add.description == "Adds an ignore rule for /.intentdiff"

    remove = _diff(differ, "/target\n*.log\n", "/target\n").changes[0]
    assert remove.description == "Stops ignoring *.log"

    negate = _diff(differ, "build/\n", "build/\n!build/keep.txt\n").changes[0]
    assert negate.description == "Adds an exception for build/keep.txt (no longer ignored)"
