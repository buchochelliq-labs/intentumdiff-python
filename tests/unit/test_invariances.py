"""Invariance behaviour as served by core (#301).

The invariance catalogue (rule ids, evaluators, admissible languages, status) and its schema
are owned by intentumdiff-core (``crates/rust-core-host/invariances``, checked by core's
``invariance_catalogue_contract`` test). The binding ships no copy of it; these tests check
core's served output against explicit expected results.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intentumdiff import ChangeGroupKind, SemanticDiffer

ROOT = Path(__file__).parents[2]

# Explicit expectation: the catalogue rules core marks disabled. A disabled rule id must
# never appear as a served change group.
_CORE_DISABLED_RULE_IDS = ("core.number_literal.canonical_value.safe", "catalog.parser_only_languages")


def _diff_css(old: str, new: str):
    return SemanticDiffer().diff_strings(old, new, "style.css", language_hint="css")


@pytest.mark.parametrize(
    ("old", "new", "filename", "language"),
    [
        (".button { color: blue; }", ".button { color: #0000ff; }", "style.css", "css"),
        ("ratio = 1.0\n", "ratio = 1.00\n", "example.py", "python"),
        ("ratio = 1.5e3\n", "ratio = 1500.0\n", "example.py", "python"),
    ],
)
def test_disabled_catalogue_rules_do_not_surface_from_rust(old, new, filename, language):
    diff = SemanticDiffer().diff_strings(old, new, filename, language_hint=language)
    assert not any(group.rule_id in _CORE_DISABLED_RULE_IDS for group in diff.change_groups)


def test_binding_ships_no_invariance_catalogue():
    """#301: evaluator/language/status admissibility is core's; no Python copy remains."""
    package = ROOT / "src" / "intentumdiff"
    assert not (package / "invariances").exists()
    assert not list(package.rglob("rules.yaml"))
    assert not list(package.rglob("rules.schema.json"))


def test_css_blue_and_hex_blue_are_ignored_with_explainable_evidence():
    diff = _diff_css(".button { color: blue; }", ".button { color: #0000ff; }")

    assert diff.has_semantic_changes is False
    assert diff.changes == []

    group = next(
        group
        for group in diff.change_groups
        if group.rule_id == "css.color.canonical_equivalence"
    )
    assert group.kind == ChangeGroupKind.IGNORED_STYLE
    assert group.old_node_ids
    assert group.new_node_ids
    assert group.metadata["equivalence_kind"] == "canonical_value_equivalence"
    assert group.metadata["canonical_old"] == "srgb(0,0,255,1)"
    assert group.metadata["canonical_new"] == "srgb(0,0,255,1)"
    assert group.metadata["old_label"] == "blue"
    assert group.metadata["new_label"] == "#0000ff"
    assert group.metadata["reason"]

    ignored = diff.metadata["ignored_style_changes"]
    assert ignored[0]["rule_id"] == "css.color.canonical_equivalence"
    assert ignored[0]["reason"] == group.metadata["reason"]


def test_python_comment_whitespace_style_only_has_explainable_source_evidence():
    diff = SemanticDiffer().diff_strings(
        "def answer():\n    return 42\n",
        "# extra context\n\ndef answer():\n    return 42\n",
        "example.py",
        language_hint="python",
    )

    assert diff.is_style_only is True
    assert diff.has_semantic_changes is False
    assert diff.changes == []
    group = next(
        group
        for group in diff.change_groups
        if group.rule_id == "generic.style_only_shortcut.source_equivalence"
    )
    assert group.kind == ChangeGroupKind.IGNORED_STYLE
    assert group.old_node_ids == []
    assert group.new_node_ids == []
    assert group.metadata["index_space"] == "style_only_shortcut"
    assert group.metadata["evidence_depth"] == "source_span"
    assert group.metadata["old_span"] == [0, 0]
    assert group.metadata["new_span"][1] > group.metadata["new_span"][0]
    assert diff.metadata["ignored_style_changes"][0]["rule_id"] == group.rule_id


def test_identical_style_only_diff_has_no_ignored_evidence():
    source = "def answer():\n    return 42\n"

    diff = SemanticDiffer().diff_strings(source, source, "example.py", language_hint="python")

    assert diff.is_style_only is True
    assert diff.changes == []
    assert diff.change_groups == []
    assert "ignored_style_changes" not in diff.metadata


def test_multiple_style_only_hunks_are_individually_inspectable():
    diff = SemanticDiffer().diff_strings(
        "x = 1\n\ny = 2\n",
        "# first\nx = 1\n\n# second\ny = 2\n",
        "example.py",
        language_hint="python",
    )

    groups = [
        group
        for group in diff.change_groups
        if group.rule_id == "generic.style_only_shortcut.source_equivalence"
    ]
    assert diff.is_style_only is True
    assert len(groups) >= 2
    assert [group.metadata["occurrence"] for group in groups] == list(range(len(groups)))
    assert all(group.metadata["evidence_depth"] == "source_span" for group in groups)


def test_integer_literal_equivalence_is_ignored_with_explainable_evidence():
    diff = SemanticDiffer().diff_strings(
        "answer = 1_000\n",
        "answer = 1000\n",
        "example.py",
        language_hint="python",
    )

    assert diff.has_semantic_changes is False
    assert diff.changes == []
    group = next(
        group
        for group in diff.change_groups
        if group.rule_id == "core.integer_literal.canonical_value.safe"
    )
    assert group.kind == ChangeGroupKind.IGNORED_STYLE
    assert group.metadata["canonical_old"] == "int(1000)"
    assert group.metadata["canonical_new"] == "int(1000)"
    assert group.metadata["old_label"] == "1_000"
    assert group.metadata["new_label"] == "1000"


def test_python_quote_only_string_diff_has_source_span_literal_evidence():
    diff = SemanticDiffer().diff_strings(
        "host = 'semanticdiff.com'\n",
        'host = "semanticdiff.com"\n',
        "example.py",
        language_hint="python",
    )

    assert diff.has_semantic_changes is False
    assert diff.changes == []
    group = next(
        group
        for group in diff.change_groups
        if group.rule_id == "core.string_literal.decoded_value.safe"
    )
    assert group.kind == ChangeGroupKind.IGNORED_STYLE
    assert group.metadata["canonical_old"] == "string(semanticdiff.com)"
    assert group.metadata["canonical_new"] == "string(semanticdiff.com)"
    assert group.metadata["old_label"] == "'semanticdiff.com'"
    assert group.metadata["new_label"] == '"semanticdiff.com"'
    assert group.metadata["evidence_depth"] == "source_span"
    assert group.metadata["old_span"] == [7, 25]
    assert group.metadata["new_span"] == [7, 25]


def test_interpolated_strings_are_not_hidden_as_plain_string_equivalence():
    diff = SemanticDiffer().diff_strings(
        'name = "semanticdiff.com"\n',
        'name = f"semanticdiff.com"\n',
        "example.py",
        language_hint="python",
    )

    assert not any(
        group.rule_id == "core.string_literal.decoded_value.safe"
        for group in diff.change_groups
    )


def test_css_short_hex_and_rgb_blue_are_ignored():
    diff = _diff_css(".button { color: #00f; }", ".button { color: rgb(0 0 255); }")

    assert diff.has_semantic_changes is False
    assert diff.changes == []
    assert any(
        group.metadata.get("canonical_old") == "srgb(0,0,255,1)"
        for group in diff.change_groups
    )


def test_css_hex_case_only_change_is_ignored():
    diff = _diff_css(".button { color: #FF0000; }", ".button { color: #ff0000; }")

    assert diff.has_semantic_changes is False
    assert diff.changes == []
    assert any(
        group.metadata.get("old_label") == "#FF0000"
        and group.metadata.get("new_label") == "#ff0000"
        for group in diff.change_groups
    )


def test_css_blue_to_red_remains_meaningful():
    diff = _diff_css(".button { color: blue; }", ".button { color: red; }")

    assert diff.has_semantic_changes is True
    assert diff.changes
    assert not any(
        group.rule_id == "css.color.canonical_equivalence"
        for group in diff.change_groups
    )


def test_css_variable_color_is_not_hidden_as_equivalent():
    diff = _diff_css(".button { color: var(--brand-blue); }", ".button { color: blue; }")

    assert diff.has_semantic_changes is True
    assert diff.changes
    assert not any(
        group.rule_id == "css.color.canonical_equivalence"
        for group in diff.change_groups
    )


def test_multiple_ignored_colors_remain_individually_inspectable():
    old = ".button { color: blue; background: #FF0000; }"
    new = ".button { color: #00f; background: #ff0000; }"

    diff = _diff_css(old, new)

    groups = [
        group
        for group in diff.change_groups
        if group.rule_id == "css.color.canonical_equivalence"
    ]
    assert diff.has_semantic_changes is False
    assert len(groups) == 2
    assert [group.metadata["occurrence"] for group in groups] == [0, 1]
    assert {group.metadata["old_label"] for group in groups} == {"blue", "#FF0000"}
    assert {group.metadata["new_label"] for group in groups} == {"#00f", "#ff0000"}
    assert all(group.old_node_ids and group.new_node_ids for group in groups)
