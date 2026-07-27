"""Issue-inspired regressions mined from public SemanticDiff feedback."""

from __future__ import annotations

import json

from intentdiff import SemanticDiffer
from intentdiff.core.models import ChangeGroupKind, ChangeType
from tests.unit.diff_sanity import assert_no_identical_positioned_source_modifications


def _diff(old: str, new: str, *, language: str, filename: str):
    diff = SemanticDiffer().diff_strings(
        old,
        new,
        filename=filename,
        language_hint=language,
    )
    assert diff.language == language
    assert not diff.is_fallback
    assert diff.parse_errors == []
    _assert_first_party_wasm_parser(diff, language)
    return diff


def _assert_first_party_wasm_parser(diff, language: str) -> None:
    if language == "python":
        rust_core = diff.metadata.get("rust_core")
        assert isinstance(rust_core, dict), language
        assert rust_core.get("used") is True, language
        assert str(rust_core.get("engine", "")).startswith("rust_core_"), language
        assert diff.metadata.get("engine_owner") in {None, "rust"}, language
        return

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry, language
    process_calls = [
        call
        for call in telemetry["calls"]
        if call["function"] == "process"
    ]
    assert process_calls, language
    assert all(call["provenance"] == "first_party_wasm" for call in process_calls), language
    assert all(call["trusted"] is True for call in process_calls), language


def test_json_reorder_keeps_value_change_and_suppresses_unchanged_key_moves() -> None:
    diff = _diff(
        """\
{
  "a": 1,
  "b": 2,
  "items": [{"id": "one", "v": 1}, {"id": "two", "v": 2}]
}
""",
        """\
{
  "items": [{"id": "two", "v": 3}, {"id": "one", "v": 1}],
  "a": 1,
  "b": 2
}
""",
        language="json",
        filename="settings.json",
    )

    assert [change.change_type for change in diff.changes] == [ChangeType.MODIFICATION]
    assert "2" in (diff.changes[0].old_node.label if diff.changes[0].old_node else "")
    assert "3" in (diff.changes[0].new_node.label if diff.changes[0].new_node else "")
    # Truth contract: zero ADDITION/DELETION noise for the reordered keys.
    # The rule pathway differs by matcher (Rust matcher handles keyed
    # reorder natively; Python oracle emits+suppresses via
    # ``presentation.suppress_keyed_reorder_only_move``) — both satisfy
    # the observable contract. See the retired NOISE_SUPPRESSION_RETUNE doc (git history).
    assert not any(
        change.change_type in {ChangeType.ADDITION, ChangeType.DELETION}
        for change in diff.changes
    ), "expected zero add/delete noise for the JSON reorder"


def test_java_override_and_import_reorder_remain_reviewable_not_noisy() -> None:
    diff = _diff(
        """\
import java.util.List;
import java.util.Map;

class Demo {
  public String name() { return "old"; }
}
""",
        """\
import java.util.Map;
import java.util.List;

class Demo {
  @Override
  public String name() { return "new"; }
}
""",
        language="java",
        filename="Demo.java",
    )

    assert {change.change_type for change in diff.changes} == {
        ChangeType.REFACTORING,
        ChangeType.MODIFICATION,
    }
    assert any(group.kind == ChangeGroupKind.REFACTORING for group in diff.change_groups)
    # Truth contract: zero ADDITION/DELETION noise for the import+@Override
    # reorder. The rule pathway differs by matcher (Python oracle:
    # ``refinement.suppress_low_signal_reorders``; Rust: structural match).
    assert not any(
        change.change_type in {ChangeType.ADDITION, ChangeType.DELETION}
        for change in diff.changes
    ), "expected zero add/delete noise for the import+@Override reorder"


def test_csharp_async_identifier_and_modern_expression_body_parse_cleanly() -> None:
    diff = _diff(
        """\
class Demo {
  string async = "old";
  public string Name => async;
}
""",
        """\
class Demo {
  string async = "new";
  public string Name => async;
}
""",
        language="csharp",
        filename="Demo.cs",
    )

    assert [change.change_type for change in diff.changes] == [ChangeType.MODIFICATION]
    # Literal labels are source-exact including quotes since the #46 capture sweep, so
    # match the value inside the label rather than the bare word.
    assert any(
        group.kind == ChangeGroupKind.MEANINGFUL_CHANGE
        and any("old" in label for label in group.old_labels)
        and any("new" in label for label in group.new_labels)
        for group in diff.change_groups
    )


def test_javascript_automatic_semicolon_insertion_does_not_create_scaffold_noise() -> None:
    diff = _diff(
        """\
export function value() {
  return 1
}
""",
        """\
export function value() {
  return 2;
}
""",
        language="javascript",
        filename="demo.js",
    )

    assert [change.change_type for change in diff.changes] == [ChangeType.MODIFICATION]
    # Truth contract: zero ADDITION/DELETION noise for the function wrapper
    # that shifted shape after ASI. The rule pathway differs by matcher
    # (Python oracle: ``presentation.suppress_same_label_add_delete_pair``;
    # Rust: structural-hash match avoids the noise entirely).
    assert not any(
        change.change_type in {ChangeType.ADDITION, ChangeType.DELETION}
        for change in diff.changes
    ), "expected zero add/delete noise for the ASI-shifted function wrapper"


def test_css_attribute_selector_value_change_stays_anchored() -> None:
    diff = _diff(
        """\
.card[data-state="open"] {
  color: red;
}
""",
        """\
.card[data-state="open"] {
  color: green;
}
""",
        language="css",
        filename="style.css",
    )

    assert [change.change_type for change in diff.changes] == [ChangeType.MODIFICATION]
    assert "red" in (diff.changes[0].old_node.label if diff.changes[0].old_node else "")
    assert "green" in (diff.changes[0].new_node.label if diff.changes[0].new_node else "")


def test_scss_use_alias_and_variable_change_parse_cleanly() -> None:
    diff = _diff(
        """\
@use "theme" as t;

.card {
  color: t.$red;
}
""",
        """\
@use "theme" as theme;

.card {
  color: theme.$green;
}
""",
        language="scss",
        filename="style.scss",
    )

    assert [change.change_type for change in diff.changes] == [ChangeType.MODIFICATION]
    assert "red" in (diff.changes[0].old_node.label if diff.changes[0].old_node else "")
    assert "green" in (diff.changes[0].new_node.label if diff.changes[0].new_node else "")


def test_cpp_preprocessor_macro_and_template_change_parse_cleanly() -> None:
    old = """\
#define LIMIT 4

template <typename T>
T clamp(T value) {
  if (value > LIMIT) {
    return LIMIT;
  }
  return value;
}
"""
    new = """\
#define LIMIT 8

template <typename T>
T clamp(T value) {
  if (value > LIMIT) {
    return LIMIT;
  }
  return value;
}
"""
    diff = _diff(old, new, language="cpp", filename="include/config.hpp")

    assert diff.changes
    assert not diff.is_fallback
    assert_no_identical_positioned_source_modifications(diff, old, new)


def test_c_large_function_move_keeps_review_evidence_without_fallback() -> None:
    old = """\
int alpha(void) {
  int total = 0;
  total += 1;
  total += 2;
  total += 3;
  return total;
}

int beta(void) {
  return 42;
}
"""
    new = """\
int beta(void) {
  return 42;
}

int alpha(void) {
  int total = 0;
  total += 1;
  total += 2;
  total += 4;
  return total;
}
"""
    diff = _diff(old, new, language="c", filename="src/math.c")

    assert diff.changes or diff.change_groups
    assert not diff.is_fallback
    assert any(
        change.change_type in {ChangeType.MOVE, ChangeType.MODIFICATION}
        for change in diff.changes
    )
    assert_no_identical_positioned_source_modifications(diff, old, new)


def test_cpp_release_depth_contract_covers_macro_template_and_large_move_evidence() -> None:
    macro_template = _diff(
        """\
#define LIMIT 4

template <typename T>
T clamp(T value) {
  if (value > LIMIT) {
    return LIMIT;
  }
  return value;
}
""",
        """\
#define LIMIT 8

template <typename T>
T clamp(T value) {
  if (value > LIMIT) {
    return LIMIT;
  }
  return value;
}
""",
        language="cpp",
        filename="include/config.hpp",
    )
    large_move = _diff(
        """\
int alpha(void) {
  int total = 0;
  total += 1;
  total += 2;
  total += 3;
  return total;
}

int beta(void) {
  return 42;
}
""",
        """\
int beta(void) {
  return 42;
}

int alpha(void) {
  int total = 0;
  total += 1;
  total += 2;
  total += 4;
  return total;
}
""",
        language="c",
        filename="src/math.c",
    )

    assert macro_template.metadata.get("engine_telemetry")
    assert large_move.metadata.get("engine_telemetry")
    assert any(
        change.change_type in {ChangeType.MODIFICATION, ChangeType.MOVE}
        for diff in (macro_template, large_move)
        for change in diff.changes
    )
    assert_no_identical_positioned_source_modifications(
        macro_template,
        "#define LIMIT 4\n",
        "#define LIMIT 8\n",
    )


def test_cpp_compile_commands_context_is_reported_for_release_depth(
    tmp_path,
    monkeypatch,
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "src/math.cpp",
                    "arguments": [
                        "clang++",
                        "-std=c++20",
                        "-Iinclude",
                        "-DUSE_FAST=1",
                        "-c",
                        "src/math.cpp",
                    ],
                }
            ]
        ),
        encoding="utf8",
    )
    monkeypatch.chdir(tmp_path)

    diff = _diff(
        """\
#include "limits.h"
#define LIMIT 4
int clamp(int value) { return value > LIMIT ? LIMIT : value; }
""",
        """\
#include "limits.h"
#define LIMIT 8
int clamp(int value) { return value > LIMIT ? LIMIT : value; }
""",
        language="cpp",
        filename="src/math.cpp",
    )

    compile_metadata = diff.metadata.get("compile_commands")
    assert isinstance(compile_metadata, dict)
    assert compile_metadata["database"] == "compile_commands.json"
    assert compile_metadata["file"] == "src/math.cpp"
    assert compile_metadata["standard"] == "c++20"
    assert compile_metadata["defines"] == ["USE_FAST=1"]
    assert compile_metadata["include_dirs"] == ["include"]
    assert compile_metadata["fingerprint"]


def test_python_nested_scope_metadata_is_available_for_breadcrumbs() -> None:
    diff = _diff(
        """\
class Demo:
    def run(self):
        value = 1
        return value
""",
        """\
class Demo:
    def run(self):
        value = 2
        return value
""",
        language="python",
        filename="demo.py",
    )

    scope_trails = diff.metadata.get("scope_trails")

    assert isinstance(scope_trails, dict)
    flattened = [
        " > ".join(item["trail"])
        for side in ("old", "new")
        for item in scope_trails.get(side, [])
    ]
    assert any("Demo" in trail and "run" in trail for trail in flattened)
