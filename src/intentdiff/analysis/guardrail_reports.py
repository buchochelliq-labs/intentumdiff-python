"""CI report helpers for protected semantic guardrails."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from intentdiff.core.models import (
    GuardrailCheckResult,
    GuardrailSeverity,
    GuardrailViolation,
    NodePosition,
    SemanticDiff,
)


def build_guardrail_check_result(
    violations: Iterable[GuardrailViolation],
    *,
    checked_files: int,
    strict: bool,
    metadata: Mapping[str, Any] | None = None,
) -> GuardrailCheckResult:
    """Build a counted, JSON-safe guardrail check result."""

    items = list(violations)
    immutable_count = sum(
        violation.severity == GuardrailSeverity.IMMUTABLE for violation in items
    )
    important_count = sum(
        violation.severity == GuardrailSeverity.IMPORTANT for violation in items
    )
    return GuardrailCheckResult(
        violations=items,
        violation_count=len(items),
        immutable_count=immutable_count,
        important_count=important_count,
        checked_files=checked_files,
        strict=strict,
        passed=not (strict and immutable_count),
        metadata=metadata or {},
    )


def guardrail_result_from_diffs(
    diffs: SemanticDiff | Sequence[SemanticDiff],
    *,
    strict: bool,
    checked_files: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> GuardrailCheckResult:
    """Collect guardrail evidence from one or more semantic diffs."""

    items = [diffs] if isinstance(diffs, SemanticDiff) else list(diffs)
    violations = [
        violation
        for diff in items
        for violation in diff.guardrail_violations
    ]
    return build_guardrail_check_result(
        violations,
        checked_files=len(items) if checked_files is None else checked_files,
        strict=strict,
        metadata=metadata,
    )


def render_guardrail_json(result: GuardrailCheckResult, *, indent: int = 2) -> str:
    """Render a guardrail check result as JSON."""

    return result.model_dump_json(indent=indent)


def render_guardrail_annotations(result: GuardrailCheckResult) -> str:
    """Render GitHub Actions workflow commands for guardrail violations."""

    lines = [_annotation_line(violation) for violation in result.violations]
    return "\n".join(lines)


def render_guardrail_sarif(result: GuardrailCheckResult, *, indent: int = 2) -> str:
    """Render guardrail violations as a SARIF 2.1.0 report."""

    return json.dumps(sarif_from_guardrail_result(result), indent=indent)


def render_guardrail_terminal(result: GuardrailCheckResult) -> str:
    """Render a compact terminal summary for ``intentdiff guardrails check``."""

    if not result.violations:
        return (
            f"Guardrail check passed: {result.checked_files} file(s) checked; "
            "no protected semantic changes."
        )

    lines = [
        (
            "Guardrail check found "
            f"{result.violation_count} protected semantic change(s): "
            f"{result.immutable_count} immutable, "
            f"{result.important_count} important."
        )
    ]
    for violation in result.violations:
        location = _display_location(violation)
        lines.append(
            f"- {violation.severity.value.upper()} {location} "
            f"{violation.rule_id}: {violation.message}"
        )
    return "\n".join(lines)


def sarif_from_guardrail_result(result: GuardrailCheckResult) -> dict[str, Any]:
    """Build the SARIF object used by JSON renderers and tests."""

    rules_by_id: dict[str, dict[str, Any]] = {}
    for violation in result.violations:
        rules_by_id.setdefault(
            violation.rule_id,
            {
                "id": violation.rule_id,
                "name": violation.rule_id,
                "shortDescription": {
                    "text": violation.message or "Protected semantic path changed",
                },
                "properties": {
                    "semanticPath": violation.semantic_path,
                    "language": violation.language,
                },
            },
        )

    return {
        "version": "2.1.0",
        "$schema": (
            "https://json.schemastore.org/sarif-2.1.0.json"
        ),
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "IntentDiff",
                        "informationUri": (
                            "https://github.com/buchochelliq-labs/intentdiff"
                        ),
                        "rules": list(rules_by_id.values()),
                    }
                },
                "results": [
                    _sarif_result(violation)
                    for violation in result.violations
                ],
            }
        ],
    }


def _annotation_line(violation: GuardrailViolation) -> str:
    level = (
        "error"
        if violation.severity == GuardrailSeverity.IMMUTABLE
        else "warning"
    )
    properties = [
        f"file={_escape_annotation_property(violation.file)}",
        f"title={_escape_annotation_property(_annotation_title(violation))}",
    ]
    if violation.position is not None:
        line, column, _, _ = _region(violation.position)
        properties.extend([f"line={line}", f"col={column}"])

    message = violation.message or "Protected semantic path changed"
    if violation.old_value or violation.new_value:
        message = f"{message}: {violation.old_value!r} -> {violation.new_value!r}"
    return (
        f"::{level} {','.join(properties)}::"
        f"{_escape_annotation_message(message)}"
    )


def _annotation_title(violation: GuardrailViolation) -> str:
    return f"{violation.severity.value} guardrail {violation.rule_id}"


def _escape_annotation_message(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _escape_annotation_property(value: str) -> str:
    return (
        _escape_annotation_message(value)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _sarif_result(violation: GuardrailViolation) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": violation.rule_id,
        "level": (
            "error"
            if violation.severity == GuardrailSeverity.IMMUTABLE
            else "warning"
        ),
        "message": {
            "text": violation.message or "Protected semantic path changed",
        },
        "locations": [_sarif_location(violation)],
        "properties": {
            "semanticPath": violation.semantic_path,
            "language": violation.language,
            "nodeType": violation.node_type,
        },
    }
    if violation.old_value or violation.new_value:
        result["properties"]["oldValue"] = violation.old_value
        result["properties"]["newValue"] = violation.new_value
    return result


def _sarif_location(violation: GuardrailViolation) -> dict[str, Any]:
    physical: dict[str, Any] = {
        "artifactLocation": {"uri": violation.file},
    }
    if violation.position is not None:
        start_line, start_col, end_line, end_col = _region(violation.position)
        physical["region"] = {
            "startLine": start_line,
            "startColumn": start_col,
            "endLine": end_line,
            "endColumn": end_col,
        }
    return {"physicalLocation": physical}


def _region(position: NodePosition) -> tuple[int, int, int, int]:
    return (
        max(1, position.start_line),
        max(1, position.start_col),
        max(1, position.end_line),
        max(1, position.end_col),
    )


def _display_location(violation: GuardrailViolation) -> str:
    if violation.position is None:
        return f"{violation.file}::{violation.semantic_path}"
    line, column, _, _ = _region(violation.position)
    return f"{violation.file}:{line}:{column}::{violation.semantic_path}"
