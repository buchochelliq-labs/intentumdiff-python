"""Competitive comparison fixtures inspired by real-world online examples."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from intentdiff import SemanticDiffer
from intentdiff.core.models import SemanticDiff
from tests.unit.diff_sanity import assert_no_identical_positioned_source_modifications

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "semanticdiff_competitive_scenarios.json"
)
_ALLOWED_LABELS = {"parity", "differentiator", "quality-gap", "test-candidate"}
_SEMANTICDIFF_COMMANDS = {
    "semanticdiff.show",
    "semanticdiff.show-alt",
    "semanticdiff.hide",
    "semanticdiff.hideComments",
    "semanticdiff.showComments",
    "semanticdiff.previousChange",
    "semanticdiff.nextChange",
    "semanticdiff.openFile",
    "semanticdiff.recomputeDiff",
    "semanticdiff.collapseContextLines",
    "semanticdiff.expandContextLines",
    "semanticdiff.makeDefault",
    "semanticdiff.printState",
    "semanticdiff.resetState",
}
_SEMANTICDIFF_SETTINGS = {
    "semanticdiff.defaultDiffViewer",
    "semanticdiff.closeOriginalTab",
    "semanticdiff.fallbackDiff",
    "semanticdiff.colorIcon",
    "semanticdiff.minimap",
    "semanticdiff.diff.contextLines",
    "semanticdiff.diff.hideComments",
    "semanticdiff.diff.compareMovedCode",
}


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf8"))


@pytest.fixture(scope="module")
def differ() -> SemanticDiffer:
    return SemanticDiffer()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "scenario" not in metafunc.fixturenames:
        return
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf8"))
    scenarios = data["scenarios"]
    metafunc.parametrize(
        "scenario",
        scenarios,
        ids=[scenario["id"] for scenario in scenarios],
    )


def test_competitive_registry_has_required_report_sections(
    fixture_data: dict[str, Any],
) -> None:
    assert fixture_data["metadata"]["semanticdiff_marketplace_url"].startswith("https://")
    assert fixture_data["metadata"]["semanticdiff_docs_url"].startswith("https://")
    assert set(fixture_data["labels"]) == _ALLOWED_LABELS

    command_names = {
        item["semanticdiff_command"] for item in fixture_data["command_parity"]
    }
    setting_names = {
        item["semanticdiff_setting"] for item in fixture_data["setting_parity"]
    }
    assert _SEMANTICDIFF_COMMANDS <= command_names
    assert _SEMANTICDIFF_SETTINGS <= setting_names

    assert fixture_data["semanticdiff"]["strengths"]
    assert fixture_data["semanticdiff"]["weaknesses_to_exploit"]
    assert fixture_data["separate_viewer_backlog"]["id"] == "custom-intentdiff-diff-viewer"
    assert "changed-only view" in fixture_data["separate_viewer_backlog"]["items"]
    assert fixture_data["prioritized_backlog"]
    assert fixture_data["vscode_comparison_smoke"]["manual_or_extension_harness_targets"]


def test_competitive_registry_entries_are_complete(
    fixture_data: dict[str, Any],
) -> None:
    seen_ids: set[str] = set()
    smoke_targets = set(
        fixture_data["vscode_comparison_smoke"]["manual_or_extension_harness_targets"]
    )
    scenario_ids = {scenario["id"] for scenario in fixture_data["scenarios"]}
    semanticdiff_languages = set(fixture_data["semanticdiff"]["supported_language_ids"])

    assert smoke_targets <= scenario_ids
    assert {"simple", "medium", "complex", "extreme"} <= {
        scenario["tier"] for scenario in fixture_data["scenarios"]
    }
    assert {
        "supported",
        "unsupported_by_semanticdiff",
    } <= {scenario["semanticdiff_support"] for scenario in fixture_data["scenarios"]}

    for scenario in fixture_data["scenarios"]:
        assert scenario["id"] not in seen_ids
        seen_ids.add(scenario["id"])
        assert scenario["source"]["url"].startswith("https://")
        assert scenario["source"]["type"]
        assert "synthetic" in scenario["source"]["license_note"].lower()
        assert scenario["language"]
        assert scenario["filename"]
        assert scenario["scenario_shape"]
        assert scenario["candidate_test"].startswith("test_")
        assert scenario["fixture"]["old"]
        assert scenario["fixture"]["new"]
        assert scenario["fixture"]["old"] != scenario["fixture"]["new"]
        assert set(scenario["expected"]["labels"]) <= _ALLOWED_LABELS
        assert scenario["expected"]["semantic_or_style_result"] is True
        assert scenario["expected"]["families_any_of"]
        if scenario["semanticdiff_support"] == "supported":
            assert scenario["language"] in semanticdiff_languages
        else:
            assert scenario["language"] not in semanticdiff_languages


def test_runtime_language_coverage_matrix_is_reportable(
    differ: SemanticDiffer,
    fixture_data: dict[str, Any],
) -> None:
    runtime_languages = set(differ.supported_languages())
    semanticdiff_languages = set(fixture_data["semanticdiff"]["supported_language_ids"])
    scenario_languages = {scenario["language"] for scenario in fixture_data["scenarios"]}

    matrix = {
        language: (
            "supported_by_both"
            if language in semanticdiff_languages
            else "intentdiff_only"
        )
        for language in sorted(runtime_languages)
    }
    report = {
        "runtime_language_count": len(runtime_languages),
        "scenario_language_count": len(scenario_languages),
        "intentdiff_only_count": sum(1 for value in matrix.values() if value == "intentdiff_only"),
        "semanticdiff_overlap_count": sum(
            1 for value in matrix.values() if value == "supported_by_both"
        ),
        "semanticdiff_only_languages": sorted(semanticdiff_languages - runtime_languages),
        "matrix": matrix,
    }
    _write_optional_report({"language_coverage": report})

    assert scenario_languages <= runtime_languages
    assert report["intentdiff_only_count"] > 0
    assert "databricks-workflow" in matrix
    assert matrix["databricks-workflow"] == "intentdiff_only"
    assert "hcl" in matrix
    assert matrix["hcl"] == "intentdiff_only"


def test_competitive_synthetic_fixture_diff_contract(
    differ: SemanticDiffer,
    scenario: dict[str, Any],
) -> None:
    diff = differ.diff_strings(
        scenario["fixture"]["old"],
        scenario["fixture"]["new"],
        filename=scenario["filename"],
        language_hint=scenario["language"],
    )

    report = {
        "scenario": scenario["id"],
        "language": scenario["language"],
        "tier": scenario["tier"],
        "semanticdiff_support": scenario["semanticdiff_support"],
        "metrics": _diff_metrics(diff),
    }
    _write_optional_report({"scenario": report})

    assert not diff.is_fallback, _format_diff_failure(scenario, diff)
    assert not diff.parse_errors, _format_diff_failure(scenario, diff)
    assert diff.has_semantic_changes or diff.is_style_only, _format_diff_failure(
        scenario, diff
    )
    assert diff.changes or diff.change_groups or diff.is_style_only

    if diff.has_semantic_changes:
        observed_families = set(_diff_metrics(diff)["change_families"])
        expected_families = set(scenario["expected"]["families_any_of"])
        assert observed_families & expected_families, (
            f"{scenario['id']} expected one of {sorted(expected_families)}, "
            f"observed {sorted(observed_families)}"
        )
        if scenario["expected"].get("requires_position_evidence"):
            assert _has_position_evidence(diff), _format_diff_failure(scenario, diff)

    assert_no_identical_positioned_source_modifications(
        diff,
        scenario["fixture"]["old"],
        scenario["fixture"]["new"],
    )


def _diff_metrics(diff: SemanticDiff) -> dict[str, Any]:
    families: Counter[str] = Counter(
        _change_family(_value(change.change_type)) for change in diff.changes
    )
    group_kinds: Counter[str] = Counter(_value(group.kind) for group in diff.change_groups)
    return {
        "change_count": len(diff.changes),
        "change_families": dict(sorted(families.items())),
        "group_count": len(diff.change_groups),
        "group_kinds": dict(sorted(group_kinds.items())),
        "has_semantic_changes": diff.has_semantic_changes,
        "is_style_only": diff.is_style_only,
        "parse_errors": list(diff.parse_errors),
    }


def _has_position_evidence(diff: SemanticDiff) -> bool:
    return any(
        (change.old_node is not None and change.old_node.position is not None)
        or (change.new_node is not None and change.new_node.position is not None)
        for change in diff.changes
    )


def _change_family(change_type: str) -> str:
    upper = change_type.upper()
    if "ADD" in upper or "INSERT" in upper:
        return "addition"
    if "DELETE" in upper or "REMOVE" in upper:
        return "deletion"
    if "MOVE" in upper or "RENAME" in upper or "REFACTOR" in upper:
        return "refactoring"
    if "MOD" in upper or "UPDATE" in upper or "CHANGE" in upper:
        return "modification"
    if "STYLE" in upper:
        return "style"
    return "other"


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _format_diff_failure(scenario: dict[str, Any], diff: SemanticDiff) -> str:
    return json.dumps(
        {
            "scenario": scenario["id"],
            "language": scenario["language"],
            "source": scenario["source"]["url"],
            "metrics": _diff_metrics(diff),
        },
        indent=2,
        sort_keys=True,
    )


def _write_optional_report(report: dict[str, Any]) -> None:
    target = os.environ.get("INTENTDIFF_SEMANTICDIFF_COMPETITIVE_REPORT")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as handle:
        handle.write(json.dumps(report, sort_keys=True))
        handle.write("\n")
