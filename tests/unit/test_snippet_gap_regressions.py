"""Regression tests for the snippet gap analysis backlog."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

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

_PY_ANNOTATION_OLD = """\
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

_PY_ANNOTATION_NEW = """\
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

_GO_ENTITY_ANCHOR_OLD = """\
package main

import "fmt"

func add(a, b int) int {
\treturn a + b
}

func main() {
\tresult := add(3, 4)
\tfmt.Println(result)
}
"""

_GO_ENTITY_ANCHOR_NEW = """\
package main

import "fmt"

func add(x, y int) int {
\treturn x + y
}

func subtract(x, y int) int {
\treturn x - y
}

func main() {
\tfmt.Println(add(3, 4))
\tfmt.Println(subtract(10, 3))
}
"""

_ABAP_SHALLOW_FORM_OLD = """\
REPORT z_demo.

FORM greet.
  WRITE: 'Hello, World'.
ENDFORM.a
"""

_ABAP_SHALLOW_FORM_NEW = """\
REPORT z_demo.

FORM greet USING lv_name TYPE string.
  DATA(lv_msg) = |Hello, { lv_name }!|.
  WRITE: lv_msg.
ENDFORM.

FORM add_numbers USING a TYPE i b TYPE i CHANGING result TYPE i.
  result = a + b.
ENDFORM.
"""


def _fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _example(language: str, name: str) -> tuple[str, str, str]:
    data = _fixture()["languages"][language]
    for example in data["examples"]:
        if example["name"] == name:
            old = "\n".join(example["old_lines"]) + "\n"
            new = "\n".join(example["new_lines"]) + "\n"
            return old, new, data["filename"]
    raise AssertionError(f"missing fixture {language}/{name}")


def _diff(language: str, name: str) -> SemanticDiff:
    old, new, filename = _example(language, name)
    return SemanticDiffer().diff_strings(
        old,
        new,
        filename=filename,
        language_hint=language,
    )


def _annotation_diff() -> SemanticDiff:
    return SemanticDiffer().diff_strings(
        _PY_ANNOTATION_OLD,
        _PY_ANNOTATION_NEW,
        filename="code.py",
        language_hint="python",
    )


def _go_entity_anchor_diff() -> SemanticDiff:
    return SemanticDiffer().diff_strings(
        _GO_ENTITY_ANCHOR_OLD,
        _GO_ENTITY_ANCHOR_NEW,
        filename="code.go",
        language_hint="go",
    )


def _abap_shallow_form_diff() -> SemanticDiff:
    return SemanticDiffer().diff_strings(
        _ABAP_SHALLOW_FORM_OLD,
        _ABAP_SHALLOW_FORM_NEW,
        filename="code.abap",
        language_hint="abap",
    )


def _default_filenames(differ: SemanticDiffer) -> dict[str, str]:
    filenames: dict[str, str] = {}
    for group in differ.language_info():
        if not group.plugins:
            continue
        selected = next(
            (
                plugin
                for plugin in group.plugins
                if plugin.plugin_id == group.selected_plugin_id
            ),
            group.plugins[0],
        )
        filenames[group.language] = selected.default_filename
    return filenames


def _playground_diff(language: str) -> SemanticDiff:
    differ = SemanticDiffer()
    example = differ.playground_example(language)
    assert example is not None
    return differ.diff_strings(
        example["old"],
        example["new"],
        filename=_default_filenames(differ)[language],
        language_hint=language,
    )


def _walk(node: SemanticNode | None) -> Iterable[SemanticNode]:
    if node is None:
        return
    yield node
    yield from node.descendants()


def _labels(node: SemanticNode | None) -> list[str]:
    return [item.label for item in _walk(node) if item.label]


def _mentions(change: Change, side: str, *tokens: str) -> bool:
    node = change.old_node if side == "old" else change.new_node
    labels = _labels(node)
    return all(any(token in label for label in labels) for token in tokens)


def _changes(diff: SemanticDiff, change_type: ChangeType) -> list[Change]:
    return [change for change in diff.changes if change.change_type == change_type]


def _renames(diff: SemanticDiff, old_label: str, new_label: str) -> list[Change]:
    return [
        change
        for change in diff.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind
        in {
            RefactoringKind.RENAME_SYMBOL,
            RefactoringKind.RENAME_CLASS,
            RefactoringKind.RENAME_METHOD,
            RefactoringKind.RENAME_VARIABLE,
        }
        and _mentions(change, "old", old_label)
        and _mentions(change, "new", new_label)
    ]


def _has_group(diff: SemanticDiff, kind: ChangeGroupKind, *labels: str) -> bool:
    for group in diff.change_groups:
        if group.kind != kind:
            continue
        group_labels = [*group.old_labels, *group.new_labels]
        if all(any(label in item for item in group_labels) for label in labels):
            return True
    return False


def _groups_with_rule(diff: SemanticDiff, rule_id: str):
    return [group for group in diff.change_groups if group.rule_id == rule_id]


def _assert_no_types(diff: SemanticDiff, *types: ChangeType) -> None:
    offenders = [change for change in diff.changes if change.change_type in types]
    assert not offenders


def test_stage1_final_move_and_refactoring_groups() -> None:
    moved = _diff("python", "Moved Code")
    assert _has_group(moved, ChangeGroupKind.MOVED_CODE, "calc_hash")
    assert any(
        group.metadata.get("index_space") == "final_changes"
        for group in moved.change_groups
    )

    annotation = _annotation_diff()
    assert _has_group(annotation, ChangeGroupKind.REFACTORING, "greet")
    assert _has_group(annotation, ChangeGroupKind.REFACTORING, "a", "x")

    js_renames = _diff("javascript", "Renames")
    assert _has_group(js_renames, ChangeGroupKind.REFACTORING, "dir", "directory")

    csharp_renames = _diff("csharp", "Renames")
    assert _has_group(csharp_renames, ChangeGroupKind.REFACTORING, "srv", "server")


def test_stage2_python_renames_are_deduped_and_rename_only_signature_is_hidden() -> None:
    diff = _diff("python", "Renames")

    assert len(_renames(diff, "addr", "address")) == 1
    assert len(_renames(diff, "start", "start_time")) == 1
    assert not [
        change
        for change in diff.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind == RefactoringKind.CHANGE_SIGNATURE
    ]
    _assert_no_types(
        diff,
        ChangeType.MOVE,
        ChangeType.REORDER,
        ChangeType.ADDITION,
        ChangeType.DELETION,
    )
    assert not [
        group
        for group in diff.change_groups
        if group.kind == ChangeGroupKind.MEANINGFUL_CHANGE
    ]
    assert _groups_with_rule(diff, "presentation.compact_superseded_meaningful_group")


def test_stage2_python_annotation_signatures_are_preserved() -> None:
    diff = _annotation_diff()
    signature_labels = [
        change.old_node.label
        for change in diff.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind == RefactoringKind.CHANGE_SIGNATURE
        and change.old_node is not None
    ]

    assert set(signature_labels) == {"greet", "add", "__init__", "increment"}
    assert len(_renames(diff, "a", "x")) == 1
    assert len(_renames(diff, "b", "y")) == 1


def test_stage3_python_style_is_review_clean_and_keeps_style_evidence() -> None:
    diff = _diff("python", "Style Changes")

    assert len(_changes(diff, ChangeType.DELETION)) == 1
    assert _mentions(_changes(diff, ChangeType.DELETION)[0], "old", "print", "foo", "host")
    assert len(_changes(diff, ChangeType.MODIFICATION)) == 1
    assert _mentions(_changes(diff, ChangeType.MODIFICATION)[0], "old", "mergeboard.com")
    assert _mentions(_changes(diff, ChangeType.MODIFICATION)[0], "new", "semanticdiff.com")
    _assert_no_types(diff, ChangeType.MOVE, ChangeType.REORDER, ChangeType.ADDITION)
    groups = _groups_with_rule(diff, "python.formatting.call_wrapping_equivalence")
    assert groups
    assert groups[0].kind == ChangeGroupKind.IGNORED_STYLE
    assert groups[0].metadata["reason"]
    assert groups[0].old_node_ids
    assert groups[0].new_node_ids
    assert not _groups_with_rule(diff, "presentation.ignored_style.python")


def test_stage4_javascript_moved_code_has_no_add_delete_leakage() -> None:
    diff = _diff("javascript", "Moved Code")

    assert len(_changes(diff, ChangeType.MOVE)) == 1
    assert _mentions(_changes(diff, ChangeType.MOVE)[0], "old", "calc_hash")
    assert _changes(diff, ChangeType.MODIFICATION)
    assert (
        len(
            [
                change
                for change in _changes(diff, ChangeType.MODIFICATION)
                if _mentions(change, "old", "md5")
                and _mentions(change, "new", "sha256")
            ]
        )
        == 1
    )
    assert any(
        _mentions(change, "old", "readFileSync") and _mentions(change, "new", "flag")
        for change in diff.changes
    )
    _assert_no_types(diff, ChangeType.ADDITION, ChangeType.DELETION, ChangeType.REORDER)


def test_stage3_javascript_style_is_compacted() -> None:
    diff = _diff("javascript", "Style Changes")

    deletions = _changes(diff, ChangeType.DELETION)
    assert len(deletions) == 2
    assert any(_mentions(change, "old", "console", "log", "foo") for change in deletions)
    assert any(_mentions(change, "old", "console", "log", "bar") for change in deletions)
    modifications = _changes(diff, ChangeType.MODIFICATION)
    assert len(modifications) == 1
    assert _mentions(modifications[0], "old", "Oh no")
    assert _mentions(modifications[0], "new", "An error occurred")
    groups = _groups_with_rule(diff, "javascript.formatting.call_argument_wrapping_equivalence")
    assert groups
    assert groups[0].kind == ChangeGroupKind.IGNORED_STYLE
    assert groups[0].metadata["reason"]
    assert groups[0].old_node_ids
    assert groups[0].new_node_ids
    assert not _groups_with_rule(diff, "presentation.ignored_style.javascript")


def test_stage5_csharp_renames_have_no_extra_modifications() -> None:
    diff = _diff("csharp", "Renames")

    assert len(_renames(diff, "srv", "server")) == 1
    assert len(_renames(diff, "tClient", "client")) == 1
    _assert_no_types(
        diff,
        ChangeType.MODIFICATION,
        ChangeType.MOVE,
        ChangeType.REORDER,
        ChangeType.ADDITION,
        ChangeType.DELETION,
    )


def test_stage5_csharp_style_keeps_three_meaningful_changes_only() -> None:
    diff = _diff("csharp", "Style Changes")

    modifications = _changes(diff, ChangeType.MODIFICATION)
    assert len(modifications) == 3
    assert any(
        _mentions(change, "old", "25363") and _mentions(change, "new", "25362")
        for change in modifications
    )
    assert any(_mentions(change, "new", "descending") for change in modifications)
    assert any(
        _mentions(change, "old", "{0}") and _mentions(change, "new", "Name:")
        for change in modifications
    )
    _assert_no_types(
        diff,
        ChangeType.ADDITION,
        ChangeType.DELETION,
        ChangeType.MOVE,
        ChangeType.REORDER,
    )
    groups = _groups_with_rule(
        diff,
        "csharp.formatting.initializer_query_output_wrapping_equivalence",
    )
    assert groups
    assert groups[0].kind == ChangeGroupKind.IGNORED_STYLE
    assert groups[0].metadata["reason"]
    assert groups[0].old_node_ids
    assert groups[0].new_node_ids
    assert not _groups_with_rule(diff, "presentation.ignored_style.csharp")


def test_python_moved_code_reports_empty_read_condition_at_expression_level() -> None:
    diff = _diff("python", "Moved Code")

    condition_changes = [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "not_operator"
        and change.new_node.node_type == "comparison_operator"
    ]

    assert condition_changes
    assert not [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None and change.old_node.node_type == "while_statement"
    ]


def test_go_entity_anchoring_prevents_cross_scope_rename_and_shift_move_noise() -> None:
    diff = _go_entity_anchor_diff()

    assert len(_renames(diff, "a", "x")) == 1
    assert len(_renames(diff, "b", "y")) == 1
    assert not _renames(diff, "a", "subtract")
    assert not [
        change
        for change in _changes(diff, ChangeType.MOVE)
        if _mentions(change, "old", "main") or _mentions(change, "new", "main")
    ]

    subtract_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "function_declaration"
        and change.new_node.label == "subtract"
    ]
    assert len(subtract_additions) == 1
    # Anti-spray guard: the subtract function's own subtree must not leak as extra additions.
    # (This previously asserted NO other addition mentions "subtract" at all, which pinned an
    # under-reporting bug: the genuinely new `fmt.Println(subtract(10, 3))` call statement in
    # main — and the deleted `result := add(3, 4)` — were silently swallowed by the
    # cross-entity matching defects fixed in issue #31. New code that CALLS subtract is a real
    # addition and must surface; only descendant leakage of the added function is noise.)
    added_subtract_id = subtract_additions[0].new_node.id
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.id != added_subtract_id
        and change.new_node.id.startswith(f"{added_subtract_id}.")
    ]
    # The main-body rewrite must surface its real edits: the deleted assignment statement and
    # the new subtract call site (both were invisible before the issue #31 fixes).
    assert any(
        change.old_node is not None
        and change.old_node.node_type == "short_var_declaration"
        for change in _changes(diff, ChangeType.DELETION)
    )
    assert any(
        desc.label == "subtract"
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and not change.new_node.id.startswith(added_subtract_id)
        for desc in [change.new_node, *change.new_node.descendants()]
    )


def test_abap_changed_form_is_not_suppressed_as_stable_noise() -> None:
    diff = _abap_shallow_form_diff()

    assert not diff.is_fallback
    assert not diff.parse_errors
    # The changed form must be ANCHORED, not suppressed: a meaningful group on GREET plus the
    # concrete statement-level edits beneath it. (Reformulated 2026-07-06 for issue #20: the
    # old assertions demanded a form-level MODIFICATION produced by fabricate-a-MOVE-then-
    # demote machinery — with clean same-id entity matching no MOVE exists to demote, and the
    # honest shape is the entity-anchored group + fine-grained changes.)
    greet_groups = [
        group
        for group in diff.change_groups
        if group.kind == ChangeGroupKind.MEANINGFUL_CHANGE
        and group.rule_id == "refinement.entity_child_content_changed"
        and "GREET" in [*group.old_labels, *group.new_labels]
    ]
    assert len(greet_groups) == 1, diff.change_groups
    assert not _changes(diff, ChangeType.MOVE)
    greet_signature_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "signature"
        and _mentions(change, "new", "LV_NAME")
    ]
    assert len(greet_signature_additions) == 1

    greet_data_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "data_declaration"
        and change.new_node.label == "LV_MSG"
    ]
    assert len(greet_data_additions) == 1

    greet_write_modifications = [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "write_statement"
        and change.new_node.node_type == "write_statement"
        and change.old_node.label == "'Hello, World'"
        and change.new_node.label == "lv_msg"
    ]
    assert len(greet_write_modifications) == 1

    add_numbers = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "form"
        and change.new_node.label == "ADD_NUMBERS"
    ]
    assert len(add_numbers) == 1
    assert add_numbers[0].new_node is not None
    added_form_id = add_numbers[0].new_node.id
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.id.startswith(f"{added_form_id}.")
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None
        and change.old_node.node_type == "form"
        and change.old_node.label == "GREET"
    ]


def test_powershell_entity_anchoring_keeps_shifted_functions_review_clean() -> None:
    diff = _playground_diff("powershell")

    assert not [
        change
        for change in _changes(diff, ChangeType.MOVE)
        if _mentions(change, "old", "Add-Numbers") or _mentions(change, "new", "Add-Numbers")
    ]
    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "function_statement"
        and change.new_node.label == "Multiply-Numbers"
    ]
    assert len(multiply_additions) == 1
    added_id = multiply_additions[0].new_node.id
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.id != added_id
        and change.new_node.id.startswith(f"{added_id}.")
    ]


def test_dart_function_signature_anchoring_suppresses_body_scaffold_churn() -> None:
    diff = _playground_diff("dart")

    assert len(_renames(diff, "a", "x")) == 1
    assert len(_renames(diff, "b", "y")) == 1
    assert not _changes(diff, ChangeType.MOVE)
    assert not [
        change
        for change in [*_changes(diff, ChangeType.ADDITION), *_changes(diff, ChangeType.DELETION)]
        if (change.new_node or change.old_node).node_type
        in {"function_body", "block", "return_statement", "additive_expression"}
    ]
    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        # The #46/#72 merge: an added routine is ONE function_definition wrapper.
        and change.new_node.node_type == "function_definition"
        and change.new_node.label == "multiply"
    ]
    assert len(multiply_additions) == 1


def test_delphi_added_routine_is_compact() -> None:
    diff = _playground_diff("delphi")

    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "defProc"
        and change.new_node.label == "Multiply"
    ]
    assert len(multiply_additions) == 1
    added_id = multiply_additions[0].new_node.id
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.id != added_id
        and change.new_node.id.startswith(f"{added_id}.")
    ]


def test_asm_statement_profile_preserves_operand_change_and_compact_additions() -> None:
    diff = _playground_diff("asm")

    operand_changes = [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "instruction"
        and change.old_node.label == "mov rdx, 14"
        and change.new_node.label == "mov rdx, len"
    ]
    assert len(operand_changes) == 1

    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {"len equ $ - msg", "print_msg", "ret", "call print_msg"} <= added_labels
    assert not [
        change
        for change in [*_changes(diff, ChangeType.ADDITION), *_changes(diff, ChangeType.DELETION)]
        if (change.new_node or change.old_node).node_type == "instruction"
        and "mov rdx" in (change.new_node or change.old_node).label
    ]


def test_bash_statement_profile_suppresses_expansion_churn_and_labels_commands() -> None:
    diff = _playground_diff("bash")

    assignment_changes = [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "variable_assignment"
        and change.old_node.label == "NAME=$1"
        and change.new_node.label == "NAME=${1:-World}"
    ]
    assert len(assignment_changes) == 1

    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {"set -euo pipefail", "greet", 'greet "$NAME"'} <= added_labels
    assert "-euo" not in added_labels
    assert "command" not in added_labels
    assert not [
        change
        for change in [*_changes(diff, ChangeType.ADDITION), *_changes(diff, ChangeType.DELETION)]
        if (change.new_node or change.old_node).node_type
        in {"expansion", "simple_expansion", "word", "string"}
    ]


def test_delphi_statement_profile_compacts_changed_greet_expression() -> None:
    diff = _playground_diff("delphi")

    greet_statement_changes = [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and "WriteLn('Hello, ' + Name)" in change.old_node.label
        and "WriteLn(Format('Hello, %s!', [Name]))" in change.new_node.label
    ]
    assert len(greet_statement_changes) == 1
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None and change.old_node.node_type == "exprBinary"
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "statement"
        and change.new_node.label == "statement"
    ]
    assert not [
        change
        for change in [*_changes(diff, ChangeType.ADDITION), *_changes(diff, ChangeType.DELETION)]
        if (change.new_node or change.old_node).node_type == "moduleName"
        and (change.new_node or change.old_node).label == "Demo"
    ]

    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert "WriteLn(Multiply(2, 3))" in added_labels


def test_rust_entrypoint_is_not_moved_when_new_function_is_inserted() -> None:
    diff = _playground_diff("rust")

    assert not [
        change
        for change in _changes(diff, ChangeType.MOVE)
        if _mentions(change, "old", "main") or _mentions(change, "new", "main")
    ]
    cube_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "function_item"
        and change.new_node.label == "cube"
    ]
    assert len(cube_additions) == 1


def test_groovy_scope_guard_rejects_cross_entity_rename_guesses() -> None:
    diff = _playground_diff("groovy")

    assert not _renames(diff, "a", "name")
    assert not _renames(diff, "b", "x")
    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "method_declaration"
        and change.new_node.label == "multiply"
    ]
    assert len(multiply_additions) == 1


def test_r_scoped_parameter_renames_do_not_emit_anonymous_signature_churn() -> None:
    diff = _playground_diff("r")

    assert len(_renames(diff, "a", "x")) == 1
    assert len(_renames(diff, "b", "y")) == 1
    assert not [
        change
        for change in diff.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind == RefactoringKind.CHANGE_SIGNATURE
        and change.old_node is not None
        and change.old_node.label == "(function)"
    ]
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and "multiply" in _labels(change.new_node)
    ]


def test_scoped_parameter_rename_guard_keeps_lua_squirrel_and_ruby_positive_cases() -> None:
    for language in ("lua", "squirrel", "ruby"):
        diff = _playground_diff(language)
        assert len(_renames(diff, "a", "x")) == 1
        assert len(_renames(diff, "b", "y")) == 1
        assert not _renames(diff, "a", "name")


def test_clojure_function_forms_anchor_by_defined_symbol() -> None:
    diff = _playground_diff("clojure")

    assert not [
        change
        for change in _changes(diff, ChangeType.MOVE)
        if change.old_node is not None and change.old_node.node_type == "list_lit"
    ]
    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "list_lit"
        and change.new_node.label == "multiply"
    ]
    assert len(multiply_additions) == 1


def test_elixir_definition_calls_anchor_without_parameter_leakage() -> None:
    diff = _playground_diff("elixir")

    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if _mentions(change, "old", "Greeter")
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "identifier"
        and change.new_node.label in {"x", "y"}
    ]
    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "call"
        and change.new_node.label == "multiply"
    ]
    assert len(multiply_additions) == 1


def test_haskell_signature_and_function_addition_is_compact() -> None:
    diff = _playground_diff("haskell")

    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "signature"
        and change.new_node.label == "multiply"
    ]
    multiply_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "function"
        and change.new_node.label == "multiply"
    ]
    assert len(multiply_additions) == 1


def test_perl_named_subroutines_are_not_reported_as_anonymous_moves() -> None:
    diff = _playground_diff("perl")

    assert not [
        change
        for change in _changes(diff, ChangeType.MOVE)
        if change.old_node is not None
        and change.old_node.node_type == "subroutine_declaration_statement"
    ]
    # greet's body edit must be VISIBLE. (Before the issue #23 parser fix the perl kind list
    # named a different grammar's node types, every sub body pruned to an empty block, and
    # these edits produced ZERO changes — the old `mentions greet` assertion was a proxy for
    # visibility via sub-level changes that no longer exist; the truthful shape is the
    # concrete body edits, correctly scoped inside greet.)
    assert any(
        change.change_type == ChangeType.MODIFICATION
        and _mentions(change, "old", "Hello, $name")
        and _mentions(change, "new", "${name}!")
        for change in diff.changes
    ), "greet's string edit must surface"
    assert any(
        _mentions(change, "old", "shift")
        for change in diff.changes
        if change.old_node is not None
    ), "greet's shift -> @_ signature-idiom change must surface"


def test_assemblyscript_exported_additions_use_exported_entity_name() -> None:
    diff = _playground_diff("assemblyscript")

    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and change.new_node.label == "(anonymous)"
    ]
    cube_additions = [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "function_declaration"
        and change.new_node.label == "cube"
    ]
    assert len(cube_additions) == 1


def test_zig_and_javascript_playground_examples_remain_structured() -> None:
    for language in ("zig", "javascript"):
        diff = _playground_diff(language)
        assert not diff.is_fallback
        assert not diff.parse_errors
        assert diff.changes

    zig = _playground_diff("zig")
    assert not [
        change
        for change in _changes(zig, ChangeType.MOVE)
        if _mentions(change, "old", "main") or _mentions(change, "new", "main")
    ]

    javascript = _playground_diff("javascript")
    assert not [
        change
        for change in _changes(javascript, ChangeType.DELETION)
        if change.old_node is not None
        and change.old_node.node_type == "function_declaration"
        and change.old_node.label in {"circleArea", "greet"}
    ]
    assert not [
        change
        for change in _changes(javascript, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "lexical_declaration"
        and (_mentions(change, "new", "circleArea") or _mentions(change, "new", "greet"))
    ]
    assert not [
        group
        for group in javascript.change_groups
        if group.kind == ChangeGroupKind.MOVED_CODE
        and "module" in [*group.old_labels, *group.new_labels]
        and "exports" in [*group.old_labels, *group.new_labels]
    ]
    assert not [
        change
        for change in javascript.changes
        if change.change_type == ChangeType.REFACTORING
        and change.refactoring_kind == RefactoringKind.EXTRACT_VARIABLE
    ]
    assert any(
        _mentions(change, "old", "PI", "radius")
        and _mentions(change, "new", "PI", "radius", "2")
        for change in _changes(javascript, ChangeType.MODIFICATION)
    )
    assert any(
        change.change_type == ChangeType.MODIFICATION
        and change.old_node is not None
        and change.old_node.node_type == "function_declaration"
        and change.old_node.label == "greet"
        and change.new_node is not None
        and change.new_node.node_type == "lexical_declaration"
        and _mentions(change, "new", "greet")
        for change in javascript.changes
    )


def test_json_playground_uses_keyed_paths_for_objects() -> None:
    diff = _playground_diff("json")

    assert not diff.is_fallback
    assert not diff.parse_errors
    added_pairs = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "pair"
        and change.new_node.label in {"scripts", "engines"}
    }
    assert {"scripts", "engines"} <= added_pairs
    assert any(
        _mentions(change, "old", "1.0.0") and _mentions(change, "new", "2.0.0")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert any(
        _mentions(change, "old", "index.js")
        and _mentions(change, "new", "dist/index.js")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert not [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if _mentions(change, "old", "index.js") and _mentions(change, "new", "my-app")
    ]


def test_yaml_playground_uses_keyed_paths_for_mappings() -> None:
    diff = _playground_diff("yaml")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert any(
        _mentions(change, "old", "1.0") and _mentions(change, "new", "2.0")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert any(
        _mentions(change, "old", "localhost")
        and _mentions(change, "new", "0.0.0.0")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    added_pairs = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "block_mapping_pair"
        and change.new_node.label in {"database", "timeout"}
    }
    assert {"database", "timeout"} <= added_pairs
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if _mentions(change, "old", "my-app") or _mentions(change, "old", "8080")
    ]


def test_adf_playground_anchors_existing_activity_by_identity() -> None:
    diff = _playground_diff("adf")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not [
        change
        for change in diff.changes
        if (
            change.old_node is not None
            and change.old_node.node_type == "activity"
            and "CopyData" in change.old_node.label
        )
        or (
            change.new_node is not None
            and change.new_node.node_type == "activity"
            and "CopyData" in change.new_node.label
        )
    ]
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "activity"
        and "LogSuccess" in change.new_node.label
    ]
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "parameter"
        and "targetFolder" in change.new_node.label
    ]


def test_databricks_playground_anchors_existing_tasks_by_identity() -> None:
    for language in ("databricks", "databricks-workflow"):
        diff = _playground_diff(language)

        assert not diff.is_fallback
        assert not diff.parse_errors
        assert not [
            change
            for change in diff.changes
            if (change.old_node is not None and "ingest" in _labels(change.old_node))
            or (change.new_node is not None and change.new_node.label == "ingest")
        ]
        assert [
            change
            for change in _changes(diff, ChangeType.ADDITION)
            if change.new_node is not None
            and change.new_node.node_type == "task"
            and "transform" in change.new_node.label
        ]
        assert [
            change
            for change in _changes(diff, ChangeType.ADDITION)
            if change.new_node is not None
            and change.new_node.node_type == "parameter"
            and "env" in change.new_node.label
        ]


def test_css_playground_uses_selector_paths_without_selector_moves() -> None:
    diff = _playground_diff("css")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not [
        change
        for change in _changes(diff, ChangeType.MOVE)
        if (change.old_node and "selector" in change.old_node.node_type)
        or (change.new_node and "selector" in change.new_node.node_type)
    ]
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "rule_set"
        and change.new_node.label == ".button:focus"
    ]
    assert any(
        _mentions(change, "old", "white") and _mentions(change, "new", "#ffffff")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert any(
        _mentions(change, "old", "10px") and _mentions(change, "new", "8px 16px")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )


def test_scss_playground_anchors_variables_and_rule_paths() -> None:
    diff = _playground_diff("scss")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None and change.old_node.label == "$primary"
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and change.new_node.label == "$primary"
    ]
    assert any(
        _mentions(change, "old", "blue") and _mentions(change, "new", "#2563eb")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {
        "$primary-dark",
        "$white",
        "button-base",
        ".button:hover",
        ".button:focus",
    } <= added_labels


def test_xml_playground_uses_element_paths_and_text_values() -> None:
    diff = _playground_diff("xml")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert any(
        _mentions(change, "old", "1.0") and _mentions(change, "new", "2.0")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert any(
        _mentions(change, "old", "localhost")
        and _mentions(change, "new", "0.0.0.0")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None and change.old_node.node_type == "element"
    ]
    added_elements = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and change.new_node.node_type == "element"
    }
    assert {"timeout", "database"} <= added_elements


def test_html_playground_uses_element_paths_without_cross_tag_modifications() -> None:
    diff = _playground_diff("html")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "element"
        and change.new_node.node_type == "element"
        and change.old_node.label != change.new_node.label
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None
        and change.old_node.label in {"html", "head", "body", "h1", "p"}
    ]
    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {"lang=en", "meta", "header", "main"} <= added_labels


def test_mdx_playground_anchors_existing_sections_by_heading() -> None:
    diff = _playground_diff("mdx")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None
        and change.old_node.node_type == "section"
        and change.old_node.label in {"Getting Started", "Installation"}
    ]
    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {"./components", "Callout", "bash"} <= added_labels


def test_hcl_playground_uses_resource_and_attribute_identities() -> None:
    diff = _playground_diff("hcl")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert any(
        _mentions(change, "old", "t2.micro") and _mentions(change, "new", "t3.small")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {"tags", "output instance_ip"} <= added_labels
    assert not [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "attribute"
        and change.old_node.label == "instance_type"
        and change.new_node.label == "tags"
    ]


def test_dockerfile_playground_uses_instruction_identities() -> None:
    diff = _playground_diff("dockerfile")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert any(
        change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "from_instruction"
        and change.new_node.node_type == "from_instruction"
        and _mentions(change, "old", "node:18")
        and _mentions(change, "new", "node:18-alpine")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert any(
        _mentions(change, "old", "npm install")
        and _mentions(change, "new", "npm ci --only=production")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None and change.old_node.node_type == "cmd_instruction"
    ]
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and change.new_node.node_type == "expose_instruction"
    ]


def test_puppet_playground_uses_resource_titles_and_parameter_identities() -> None:
    diff = _playground_diff("puppet")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert any(
        change.old_node is not None
        and change.new_node is not None
        and change.old_node.node_type == "attribute"
        and change.new_node.node_type == "attribute"
        and change.old_node.label == "message"
        and change.new_node.label == "message"
        for change in _changes(diff, ChangeType.MODIFICATION)
    )
    added_labels = {
        change.new_node.label
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
    }
    assert {"message", "target", "file /tmp/greeting.txt"} <= added_labels
    assert not [
        change
        for change in _changes(diff, ChangeType.MODIFICATION)
        if _mentions(change, "old", "hello") and _mentions(change, "new", "/tmp/greeting.txt")
    ]


def test_sql_playground_uses_query_clause_and_field_identities() -> None:
    diff = _playground_diff("sql")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None and change.old_node.node_type == "statement"
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and change.new_node.node_type == "statement"
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None
        and change.old_node.node_type == "field"
        and change.old_node.label in {"id", "name", "email", "active"}
    ]
    assert any(
        change.new_node is not None
        and change.new_node.node_type in {"join", "join_clause"}
        and _mentions(change, "new", "orders")
        for change in _changes(diff, ChangeType.ADDITION)
    )
    assert any(
        _mentions(change, "new", "order_total")
        for change in _changes(diff, ChangeType.ADDITION)
    )
    assert any(
        change.new_node is not None
        and change.new_node.node_type in {"order_by", "order_by_clause"}
        and _mentions(change, "new", "ORDER BY")
        for change in _changes(diff, ChangeType.ADDITION)
    )


def test_dax_playground_uses_measure_and_query_identities() -> None:
    diff = _playground_diff("dax")

    assert not diff.is_fallback
    assert not diff.parse_errors
    stable_measures = {"Sales[Total Sales]", "Sales[Sales YTD]"}
    assert not [
        change
        for change in _changes(diff, ChangeType.DELETION)
        if change.old_node is not None
        and change.old_node.node_type == "measure"
        and change.old_node.label in stable_measures
    ]
    assert not [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "measure"
        and change.new_node.label in stable_measures
    ]
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None
        and change.new_node.node_type == "measure"
        and change.new_node.label == "Sales[Sales Growth %]"
    ]
    assert any(
        _mentions(change, "new", "Growth")
        for change in _changes(diff, ChangeType.MODIFICATION)
    )


def test_power_query_m_playground_still_reports_step_changes() -> None:
    diff = _playground_diff("m")

    assert not diff.is_fallback
    assert not diff.parse_errors
    # The FilteredRows predicate edit (`> 0` -> `> 100`) must SURFACE with both sides visible —
    # never swallowed. Shape is path-dependent and both are honest: the default path splits it
    # into DELETION+ADDITION; the Rust finalize (#57) pairs it as ONE step_expression
    # MODIFICATION carrying old AND new (reformulated 2026-07-15 for the m flip — this test's
    # intent is anti-swallowing, not pinning the split shape).
    assert any(
        _mentions(change, "old", "Amount] > 0")
        for change in diff.changes
        if change.old_node is not None
    )
    assert any(
        _mentions(change, "new", "Amount] > 100")
        for change in diff.changes
        if change.new_node is not None
    )
    assert [
        change
        for change in _changes(diff, ChangeType.ADDITION)
        if change.new_node is not None and change.new_node.label == "RenamedColumns"
    ]
