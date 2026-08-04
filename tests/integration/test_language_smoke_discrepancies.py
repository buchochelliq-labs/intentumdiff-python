from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import SemanticDiff
from tests.unit.diff_sanity import assert_no_identical_positioned_source_modifications

pytestmark = pytest.mark.integration

_BASELINE_PATH = Path(__file__).parents[1] / "fixtures" / "language_smoke_discrepancies.json"


def test_supported_language_smoke_discrepancies() -> None:
    differ = SemanticDiffer()
    default_filenames = _default_filenames(differ)
    matrix: dict[str, dict[str, Any]] = {}
    discrepancies: dict[str, list[str]] = {}

    for language in differ.supported_languages():
        matrix[language] = _smoke_language(differ, default_filenames, language)
        issues = matrix[language]["issues"]
        if issues:
            discrepancies[language] = sorted(issues)

    baseline = _read_baseline()
    known = {
        language: _baseline_issues(value)
        for language, value in baseline.get("known_discrepancies", {}).items()
    }
    missing_languages = sorted(set(differ.supported_languages()) - set(matrix))
    for language in missing_languages:
        discrepancies.setdefault(language, []).append("missing_from_smoke_matrix")

    new = {
        language: sorted(set(issues) - set(known.get(language, [])))
        for language, issues in discrepancies.items()
        if set(issues) - set(known.get(language, []))
    }
    resolved = {
        language: sorted(set(issues) - set(discrepancies.get(language, [])))
        for language, issues in known.items()
        if set(issues) - set(discrepancies.get(language, []))
    }
    report = {
        "summary": _summarize_matrix(matrix),
        "new_discrepancies": new,
        "resolved_baseline_entries": resolved,
        "matrix": matrix,
    }
    _write_optional_report(report)

    assert not new and not resolved, _format_report(report)


def _smoke_language(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    language: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "language": language,
        "issues": [],
        "change_families": {},
        "change_count": 0,
        "guardrail_count": 0,
        "group_kinds": {},
        "has_semantic_changes": False,
        "is_style_only": False,
        "has_position_evidence": False,
    }
    example = differ.playground_example(language)
    if not example:
        row["issues"].append("missing_playground_example")
        return row
    old = example.get("old", "")
    new = example.get("new", "")
    if not old or not new:
        row["issues"].append("empty_playground_example")
        return row

    try:
        diff = differ.diff_strings(
            old,
            new,
            filename=default_filenames.get(language, f"example.{language}"),
            language_hint=language,
        )
    except Exception as exc:  # noqa: BLE001 - smoke report should capture the failing language.
        row["issues"].append("diff_exception")
        row["exception"] = f"{type(exc).__name__}: {exc}"
        return row

    row.update(_diff_metrics(diff))
    if diff.is_fallback:
        row["issues"].append("fallback_diff")
    if diff.parse_errors:
        row["issues"].append("parse_errors")
    if old != new and not diff.has_semantic_changes and not diff.is_style_only:
        row["issues"].append("changed_example_has_no_semantic_or_style_result")
    if diff.has_semantic_changes and not row["has_position_evidence"]:
        row["issues"].append("semantic_result_has_no_position_evidence")
    try:
        assert_no_identical_positioned_source_modifications(diff, old, new)
    except AssertionError:
        row["issues"].append("identical_positioned_source_modification")
    return row


def _diff_metrics(diff: SemanticDiff) -> dict[str, Any]:
    families: Counter[str] = Counter(_change_family(_value(change.change_type)) for change in diff.changes)
    group_kinds: Counter[str] = Counter(_value(group.kind) for group in diff.change_groups)
    return {
        "change_families": dict(sorted(families.items())),
        "change_count": len(diff.changes),
        "guardrail_count": len(diff.guardrail_violations),
        "group_kinds": dict(sorted(group_kinds.items())),
        "has_semantic_changes": diff.has_semantic_changes,
        "is_style_only": diff.is_style_only,
        "has_position_evidence": any(
            (change.old_node is not None and change.old_node.position is not None)
            or (change.new_node is not None and change.new_node.position is not None)
            for change in diff.changes
        ),
    }


def _default_filenames(differ: SemanticDiffer) -> dict[str, str]:
    filenames: dict[str, str] = {}
    for group in differ.language_info():
        selected = next(
            (
                plugin
                for plugin in group.plugins
                if plugin.plugin_id == group.selected_plugin_id
            ),
            group.plugins[0] if group.plugins else None,
        )
        if selected is not None:
            filenames[group.language] = selected.default_filename
    return filenames


def _read_baseline() -> dict[str, Any]:
    return json.loads(_BASELINE_PATH.read_text(encoding="utf8"))


def _baseline_issues(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    if isinstance(value, dict):
        return sorted(str(item) for item in value.get("issues", []))
    raise AssertionError(f"Invalid language smoke baseline entry: {value!r}")


def _write_optional_report(report: dict[str, Any]) -> None:
    target = os.environ.get("INTENTUMDIFF_LANGUAGE_SMOKE_REPORT")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf8")


def _summarize_matrix(matrix: dict[str, dict[str, Any]]) -> dict[str, Any]:
    issue_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    for row in matrix.values():
        issue_counts.update(row["issues"])
        family_counts.update(row["change_families"])
    return {
        "language_count": len(matrix),
        "issue_counts": dict(sorted(issue_counts.items())),
        "change_family_counts": dict(sorted(family_counts.items())),
    }


def _format_report(report: dict[str, Any]) -> str:
    return json.dumps(
        {
            "summary": report["summary"],
            "new_discrepancies": report["new_discrepancies"],
            "resolved_baseline_entries": report["resolved_baseline_entries"],
        },
        indent=2,
        sort_keys=True,
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
    return "other"


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))
