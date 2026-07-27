from __future__ import annotations

import json
import tomllib
from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from intentdiff import ChangeGroupKind, SemanticDiffer
from intentdiff.analysis.invariances import (
    InvarianceRuleDefinition,
    InvarianceRuleSet,
    apply_invariances,
    load_builtin_invariance_rules,
)
from intentdiff.core.models import Change, ChangeType, NodePosition, SemanticNode

ROOT = Path(__file__).parents[2]


def _diff_css(old: str, new: str):
    return SemanticDiffer().diff_strings(old, new, "style.css", language_hint="css")


def _parser_language_ids() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["entry-points"]["intentdiff.parsers"])


def _rules_schema() -> dict:
    schema_path = ROOT / "src" / "intentdiff" / "invariances" / "rules.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _node(node_id: str, node_type: str, label: str, start: int, end: int) -> SemanticNode:
    return SemanticNode(
        id=node_id,
        node_type=node_type,
        label=label,
        position=NodePosition(start_line=0, start_col=start, end_line=0, end_col=end),
        structural_hash=f"hash-{node_id}",
    )


def test_packaged_yaml_rules_load_from_package_resources():
    rule_path = resources.files("intentdiff.invariances").joinpath("rules.yaml")

    assert rule_path.is_file()
    rules = load_builtin_invariance_rules()
    assert any(rule.id == "css.color.canonical_equivalence" for rule in rules)


def test_packaged_json_schema_loads_from_package_resources():
    schema_path = resources.files("intentdiff.invariances").joinpath(
        "rules.schema.json"
    )

    assert schema_path.is_file()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$defs"]["rule"]["required"]


def test_json_schema_language_enum_matches_registered_parsers():
    schema_languages = set(_rules_schema()["$defs"]["language"]["enum"])

    assert schema_languages == _parser_language_ids()


def test_json_schema_matches_runtime_safety_contract():
    schema = _rules_schema()
    rule_schema = schema["$defs"]["rule"]
    required = set(rule_schema["required"])

    assert {
        "id",
        "languages",
        "status",
        "evaluator",
        "group_kind",
        "enabled",
        "risk",
        "equivalence_kind",
        "explanation",
    } <= required
    assert schema["$defs"]["evaluator"]["enum"] == [
        "css_color_equivalence",
        "formatting_suppression_evidence",
        "integer_literal_equivalence",
        "style_only_shortcut_evidence",
        "string_literal_equivalence",
    ]
    assert set(schema["$defs"]["status"]["enum"]) == {"implemented", "catalog"}
    assert set(schema["$defs"]["risk"]["enum"]) == {"green", "amber", "red"}
    assert rule_schema["additionalProperties"] is False


def test_invalid_evaluator_names_are_rejected():
    with pytest.raises(ValidationError, match="unknown invariance evaluator"):
        InvarianceRuleDefinition(
            id="bad.rule",
            languages=("css",),
            evaluator="os.system",
            group_kind=ChangeGroupKind.IGNORED_STYLE,
            enabled=True,
            risk="green",
            explanation="not allowed",
        )


def test_implemented_rules_require_evaluator():
    with pytest.raises(ValidationError, match="implemented invariance rules require an evaluator"):
        InvarianceRuleDefinition(
            id="missing.evaluator",
            languages=("css",),
            status="implemented",
            evaluator=None,
            group_kind=ChangeGroupKind.IGNORED_STYLE,
            enabled=True,
            risk="green",
            explanation="implemented rules need code",
        )


def test_catalog_rules_may_have_null_evaluator():
    rule = InvarianceRuleDefinition(
        id="catalog.only",
        languages=("css",),
        status="catalog",
        evaluator=None,
        group_kind=ChangeGroupKind.IGNORED_STYLE,
        enabled=False,
        risk="amber",
        explanation="documented but not executable",
        notes="safe because it cannot run",
    )

    assert rule.evaluator is None
    assert rule.status == "catalog"


def test_catalog_rules_must_be_disabled():
    with pytest.raises(ValidationError, match="catalog invariance rules must be disabled"):
        InvarianceRuleDefinition(
            id="bad.catalog.enabled",
            languages=("css",),
            status="catalog",
            evaluator=None,
            group_kind=ChangeGroupKind.IGNORED_STYLE,
            enabled=True,
            risk="amber",
            explanation="catalog rules cannot run",
        )


def test_invalid_language_ids_are_rejected():
    with pytest.raises(ValidationError, match="unknown invariance languages"):
        InvarianceRuleDefinition(
            id="bad.language",
            languages=("css", "not-a-language"),
            status="catalog",
            evaluator=None,
            group_kind=ChangeGroupKind.IGNORED_STYLE,
            enabled=False,
            risk="amber",
            explanation="language ids must match parser ids",
        )


def test_duplicate_rule_ids_are_rejected():
    rule = {
        "id": "duplicate.rule",
        "languages": ["css"],
        "status": "catalog",
        "evaluator": None,
        "enabled": False,
        "risk": "amber",
        "explanation": "duplicate ids should fail",
        "notes": "test",
    }

    with pytest.raises(ValidationError, match="duplicate invariance rule ids"):
        InvarianceRuleSet.model_validate({"version": 1, "rules": [rule, rule]})


def test_builtin_catalog_covers_every_registered_parser_language():
    covered = {
        language
        for rule in load_builtin_invariance_rules()
        for language in rule.languages
    }

    assert _parser_language_ids() - covered == set()


def test_pyproject_entry_points_match_source_tree_fallback_map():
    """A parser must be wired in BOTH pyproject.toml AND the source-tree fallback map.

    Editable installs snapshot entry-point metadata at install time (issue #61), so parser
    discovery in a dev checkout relies on registry._FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS.
    A parser added to only one of the two silently routes to `generic` in a fresh env — the
    class of breakage the gitignore/markdown additions repeatedly risked. Guard that the two
    stay in lockstep on BOTH the language-id keys and the entry-function names.
    """
    from intentdiff.plugins.registry import _FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_entries = data["project"]["entry-points"]["intentdiff.parsers"]

    pyproject_keys = set(pyproject_entries)
    fallback_keys = set(_FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS)
    assert pyproject_keys == fallback_keys, (
        "language-id keys differ: only in pyproject "
        f"{sorted(pyproject_keys - fallback_keys)}; only in fallback map "
        f"{sorted(fallback_keys - pyproject_keys)}"
    )

    pyproject_fns = {value.rsplit(":", 1)[-1] for value in pyproject_entries.values()}
    fallback_fns = set(_FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS.values())
    assert pyproject_fns == fallback_fns, (
        "entry-function names differ: only in pyproject "
        f"{sorted(pyproject_fns - fallback_fns)}; only in fallback map "
        f"{sorted(fallback_fns - pyproject_fns)}"
    )


def test_builtin_catalog_entries_have_review_metadata():
    for rule in load_builtin_invariance_rules():
        assert rule.risk in {"green", "amber", "red"}
        assert rule.equivalence_kind
        assert rule.explanation
        assert rule.source_refs or rule.notes
        if rule.status == "catalog":
            assert rule.enabled is False
            assert rule.evaluator is None


def test_disabled_rules_do_not_fire():
    disabled = InvarianceRuleDefinition(
        id="css.color.disabled",
        languages=("css",),
        evaluator="css_color_equivalence",
        group_kind=ChangeGroupKind.IGNORED_STYLE,
        enabled=False,
        risk="green",
        explanation="disabled test rule",
    )
    # White-box fixture (reformulated for the #57 python-pipeline retirement): the unit
    # under test is apply_invariances with a custom rule set, not the pipeline — feed it a
    # SYNTHETIC change that an ENABLED css color rule would fire on (blue -> #0000ff), so
    # "does not fire" is proven against a matching input.
    old_value = _node("old-color", "plain_value", "blue", 18, 22)
    new_value = _node("new-color", "color_value", "#0000ff", 18, 25)
    changes = [
        Change(
            change_type=ChangeType.MODIFICATION,
            old_node=old_value,
            new_node=new_value,
            confidence=1.0,
            description="Update plain_value('blue') -> color_value('#0000ff')",
        )
    ]

    result = apply_invariances(
        changes,
        old_tree=old_value,
        new_tree=new_value,
        old_source=".button { color: blue; }",
        new_source=".button { color: #0000ff; }",
        language="css",
        rules=(disabled,),
    )

    assert result.changes == changes
    assert result.change_groups == []


def test_catalog_only_rules_do_not_fire():
    catalog = InvarianceRuleDefinition(
        id="css.color.catalog.only",
        languages=("css",),
        status="catalog",
        evaluator=None,
        group_kind=ChangeGroupKind.IGNORED_STYLE,
        enabled=False,
        risk="green",
        explanation="catalog rules document future behavior only",
        notes="test",
    )
    # Same synthetic white-box fixture as test_disabled_rules_do_not_fire (reformulated
    # for the #57 python-pipeline retirement): a matching css color change proves the
    # catalog rule does not fire.
    old_value = _node("old-color", "plain_value", "blue", 18, 22)
    new_value = _node("new-color", "color_value", "#0000ff", 18, 25)
    state_changes = [
        Change(
            change_type=ChangeType.MODIFICATION,
            old_node=old_value,
            new_node=new_value,
            confidence=1.0,
            description="Update plain_value('blue') -> color_value('#0000ff')",
        )
    ]

    result = apply_invariances(
        state_changes,
        old_tree=old_value,
        new_tree=new_value,
        old_source=".button { color: blue; }",
        new_source=".button { color: #0000ff; }",
        language="css",
        rules=(catalog,),
    )

    assert result.changes == state_changes
    assert result.change_groups == []


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


def test_string_literal_quote_equivalence_is_ignored_with_explainable_evidence():
    old_source = "host = 'semanticdiff.com'\n"
    new_source = 'host = "semanticdiff.com"\n'
    old_node = _node("old-string", "string", "semanticdiff.com", 7, 25)
    new_node = _node("new-string", "string", "semanticdiff.com", 7, 25)
    rule = next(
        rule
        for rule in load_builtin_invariance_rules()
        if rule.id == "core.string_literal.decoded_value.safe"
    )

    result = apply_invariances(
        [
            Change(
                change_type=ChangeType.MODIFICATION,
                old_node=old_node,
                new_node=new_node,
            )
        ],
        old_tree=old_node,
        new_tree=new_node,
        old_source=old_source,
        new_source=new_source,
        language="python",
        rules=(rule,),
    )

    assert result.changes == []
    group = result.change_groups[0]
    assert group.kind == ChangeGroupKind.IGNORED_STYLE
    assert group.metadata["canonical_old"] == "string(semanticdiff.com)"
    assert group.metadata["canonical_new"] == "string(semanticdiff.com)"
    assert group.old_node_ids == ["old-string"]
    assert group.new_node_ids == ["new-string"]


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
