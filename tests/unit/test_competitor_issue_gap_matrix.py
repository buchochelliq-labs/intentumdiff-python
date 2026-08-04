from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intentumdiff.plugins.registry import PluginRegistry
import pytest

pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "docs" / "COMPETITOR_BACKLOG_COMPLETION_AUDIT.md").exists(),
    reason="monorepo release docs/artifacts not present (#82 split python repo)",
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "semanticdiff_competitor_gap_matrix.json"
)
_AUDIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "COMPETITOR_BACKLOG_COMPLETION_AUDIT.md"
)
_BACKLOG_PATH = Path(__file__).resolve().parents[2] / "docs" / "BACKLOG.md"
_BETA_GAP_BACKLOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "COMPETITOR_ISSUE_BETA_GAP_BACKLOG.md"
)
_STATUSES = {"covered", "partial", "gap", "not_planned"}
_BETA_POLICIES = {"required", "deferred_surface"}
_PROOF_STRENGTHS = {
    "exact_regression",
    "feature_contract",
    "release_verifier",
    "visual_proof_needed",
}
_IMPLEMENTATION_OWNERS = {
    "rust_core",
    "rust_wasm_parser",
    "python_shell",
    "vscode_ui",
    "release",
    "product_policy",
}
_BETA_DEFERRED_SURFACES = {
    "jetbrains-intellij-surface",
}
_BETA_VISUAL_PROOF_ROWS = {
    "custom-diff-navigation-and-layout",
    "theme-icons-and-light-mode",
}


def _matrix() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf8"))


def _audit_text() -> str:
    return _AUDIT_PATH.read_text(encoding="utf8")


def _backlog_text() -> str:
    return _BACKLOG_PATH.read_text(encoding="utf8")


def test_competitor_gap_matrix_declares_status_contract() -> None:
    matrix = _matrix()

    assert set(matrix["metadata"]["statuses"]) == _STATUSES
    assert set(matrix["metadata"]["beta_policies"]) == _BETA_POLICIES
    assert set(matrix["metadata"]["proof_strengths"]) == _PROOF_STRENGTHS
    assert set(matrix["metadata"]["implementation_owners"]) == _IMPLEMENTATION_OWNERS
    assert matrix["metadata"]["source_issue_count"] == 81
    assert matrix["metadata"]["source_open_issue_count"] == 59
    assert matrix["metadata"]["source_closed_issue_count"] == 22
    assert matrix["metadata"]["source_issues_url"].startswith("https://github.com/")
    assert matrix["metadata"]["source_scopes_url"].startswith("https://")


def test_competitor_backlog_completion_audit_tracks_every_matrix_row() -> None:
    matrix = _matrix()
    audit = _audit_text()

    assert "Matrix Status" in audit
    assert "Audit Status" in audit
    assert "Required To Close" in audit
    assert "matrix tracks whether a competitor issue has a mapped IntentumDiff response" in audit

    for entry in matrix["entries"]:
      assert f"`{entry['id']}`" in audit, entry["id"]

    deferred_rows = [
        entry["id"]
        for entry in matrix["entries"]
        if entry["beta_policy"] == "deferred_surface" or entry["status"] == "not_planned"
    ]
    assert deferred_rows == ["jetbrains-intellij-surface"]
    assert "| Deferred | `jetbrains-intellij-surface` |" in audit

    for required_phrase in [
        "TypeScript/Rust fuel runaway investigations",
        "Image diff size policy",
        "Inline editable diff",
        "overview-ruler minimap",
        "semantic hunk actions",
        "persisted snapshots",
    ]:
        assert required_phrase in audit


def test_backlog_docs_are_rebaselined_for_rc_freeze() -> None:
    # COMPETITOR_ISSUE_BETA_GAP_BACKLOG.md was retired in the issue-#50 docs
    # sweep (git history preserves it); BACKLOG.md carries the RC gate.
    backlog = _backlog_text()

    assert not _BETA_GAP_BACKLOG_PATH.exists()
    assert "## 0.0.1 RC Release Gate" in backlog
    assert "No new feature work should enter the RC" in backlog
    assert "All RC-required competitor rows are `Complete`" in backlog
    assert "JetBrains/IntelliJ is the only intentional `Deferred`" in backlog
    assert "planned_beta" not in backlog
    assert "pre_beta" not in backlog


def test_competitor_gap_matrix_classifies_every_reviewed_issue() -> None:
    matrix = _matrix()
    reviewed = set(matrix["metadata"]["issue_numbers_reviewed"])
    classified = {
        issue_number
        for entry in matrix["entries"]
        for issue_number in entry["semanticdiff_issue_numbers"]
    }

    assert len(reviewed) == matrix["metadata"]["source_issue_count"]
    assert classified == reviewed


def test_competitor_gap_matrix_entries_are_actionable() -> None:
    matrix = _matrix()
    statuses_seen: set[str] = set()
    ids_seen: set[str] = set()

    for entry in matrix["entries"]:
        assert entry["id"] not in ids_seen
        ids_seen.add(entry["id"])
        assert entry["status"] in _STATUSES
        assert entry["beta_policy"] in _BETA_POLICIES
        statuses_seen.add(entry["status"])
        assert entry["theme"]
        assert entry["intentumdiff_position"]
        assert entry["next_action"]
        assert entry["proof_strength"] in _PROOF_STRENGTHS
        assert entry["proof_target"]
        assert isinstance(entry["semanticdiff_issue_numbers"], list)
        assert isinstance(entry["regression_tests"], list)
        assert entry["implementation_owner"] in _IMPLEMENTATION_OWNERS
        assert isinstance(entry["implementation_paths"], list)
        assert entry["implementation_paths"], entry["id"]
        if entry["status"] == "covered":
            assert entry["regression_tests"], entry["id"]

    assert {"covered", "not_planned"} <= statuses_seen
    assert "partial" not in statuses_seen


def test_beta_required_competitor_rows_have_no_gaps() -> None:
    matrix = _matrix()

    for entry in matrix["entries"]:
        if entry["beta_policy"] == "required":
            assert entry["status"] == "covered", entry["id"]
            assert entry["implementation_owner"] != "product_policy", entry["id"]
            assert entry["regression_tests"], entry["id"]
            assert entry["proof_strength"] in {
                "exact_regression",
                "release_verifier",
                "visual_proof_needed",
            }, entry["id"]
            if entry["proof_strength"] == "visual_proof_needed":
                assert entry["id"] in _BETA_VISUAL_PROOF_ROWS, entry["id"]
            assert entry["proof_target"], entry["id"]
        else:
            assert entry["status"] == "not_planned", entry["id"]
            assert entry["id"] in _BETA_DEFERRED_SURFACES, entry["id"]
            assert entry["implementation_owner"] == "product_policy", entry["id"]


def test_beta_required_competitor_rows_have_concrete_proof() -> None:
    matrix = _matrix()

    vague_targets = {
        "",
        "todo",
        "tbd",
        "future",
        "manual",
        "supported",
        "covered",
    }

    for entry in matrix["entries"]:
        if entry["beta_policy"] != "required":
            continue

        proof_target = entry["proof_target"].strip()
        assert proof_target.lower() not in vague_targets, entry["id"]
        if entry["proof_strength"] == "visual_proof_needed":
            assert proof_target.startswith("Phase 4 screenshot:"), entry["id"]
        elif entry["proof_strength"] in {"exact_regression", "release_verifier"}:
            assert proof_target in entry["regression_tests"], entry["id"]
        else:
            assert (
                proof_target in entry["regression_tests"]
                or proof_target.startswith("tests/")
                or proof_target.startswith("plugins/")
            ), entry["id"]


def test_competitor_gap_matrix_proof_targets_exist() -> None:
    matrix = _matrix()
    repo_root = Path(__file__).resolve().parents[2]

    for entry in matrix["entries"]:
        for target in entry["regression_tests"]:
            path_text, _, test_name = target.partition("::")
            path = repo_root / path_text
            assert path.exists(), (entry["id"], target)
            if test_name and path.suffix == ".py":
                assert f"def {test_name}" in path.read_text(encoding="utf8"), (
                    entry["id"],
                    target,
                )
            if test_name and path.suffix == ".ts":
                assert test_name in path.read_text(encoding="utf8"), (
                    entry["id"],
                    target,
                )


def test_competitor_gap_matrix_declares_real_implementation_paths() -> None:
    matrix = _matrix()
    repo_root = Path(__file__).resolve().parents[2]

    for entry in matrix["entries"]:
        for target in entry["implementation_paths"]:
            path_text, _, symbol = target.partition("::")
            path_text, _, anchor = path_text.partition("#")
            path = repo_root / path_text
            assert path.exists(), (entry["id"], target)
            text = path.read_text(encoding="utf8") if path.is_file() else ""
            if anchor:
                assert _markdown_anchor_exists(text, anchor), (entry["id"], target)
            if symbol:
                assert path.is_file(), (entry["id"], target)
                assert symbol in text, (entry["id"], target)


def test_rust_owned_competitor_rows_have_core_or_parser_proof_paths() -> None:
    matrix = _matrix()

    for entry in matrix["entries"]:
        owner = entry["implementation_owner"]
        implementation_paths = entry["implementation_paths"]

        if owner == "rust_core":
            assert any(path.startswith("crates/rust-core-host/") for path in implementation_paths), entry["id"]
            assert any(path.startswith("src/intentumdiff/rust_core.py") for path in implementation_paths), entry["id"]
        elif owner == "rust_wasm_parser":
            assert any(path.startswith("crates/parsers/") or path == "crates/parsers" for path in implementation_paths), entry["id"]
            assert any(
                "first_party_wasm" in path
                or "test_supported_language_examples.py" in path
                or "test_competitor_issue_regressions.py" in path
                or "test_competitor_issue_gap_matrix.py" in path
                for path in [*implementation_paths, *entry["regression_tests"]]
            ), entry["id"]


def _markdown_anchor_exists(text: str, expected_anchor: str) -> bool:
    for line in text.splitlines():
        heading = line.lstrip("#").strip()
        if not heading or heading == line:
            continue
        anchor = heading.lower().replace(" ", "-").replace("`", "")
        anchor = "".join(char for char in anchor if char.isalnum() or char == "-")
        if anchor == expected_anchor:
            return True
    return False


def test_competitor_gap_matrix_tracks_windows_arm64_as_release_edge() -> None:
    matrix = _matrix()
    entries = {entry["id"]: entry for entry in matrix["entries"]}

    arm64 = entries["windows-arm64-native-wheels"]

    assert arm64["status"] == "covered"
    assert arm64["semanticdiff_issue_numbers"] == [94]
    assert "win_arm64" in arm64["intentumdiff_position"]
    assert any("arm64" in test.lower() for test in arm64["regression_tests"])


def test_parser_depth_languages_have_named_review_parsers() -> None:
    registry = PluginRegistry()
    cases = [
        ("graphql", "schema.graphql"),
        ("ocaml", "main.ml"),
        ("reasonml", "component.re"),
        ("latex", "paper.tex"),
        ("asciidoc", "README.adoc"),
        ("po", "messages.po"),
    ]

    for language, filename in cases:
        entries = registry._candidate_entries(filename, language_hint=language)  # noqa: SLF001
        assert entries, language
        assert any(language in entry.language_guesses for entry in entries)
        assert any(language in entry.entry_names for entry in entries)
        assert language != "generic"


def _assert_first_party_process_telemetry(telemetry: object, expected_plugin: str) -> None:
    assert isinstance(telemetry, dict)
    calls = telemetry.get("calls")
    assert isinstance(calls, list)
    process_calls = [
        call
        for call in calls
        if isinstance(call, dict) and call.get("function") == "process"
    ]
    assert process_calls
    assert all(call.get("provenance") == "first_party_wasm" for call in process_calls)
    assert all(call.get("trusted") is True for call in process_calls)
    assert any(expected_plugin in str(call.get("plugin", "")) for call in process_calls)
