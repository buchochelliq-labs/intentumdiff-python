"""CI report helpers for protected semantic guardrails."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from intentumdiff.core.models import (
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
    """Core's guardrail check verdict for a list of violations (``guardrail_check_result``)."""

    items = [violation.model_dump(mode="json") for violation in violations]
    return _core_check_result({"violations": items}, strict, checked_files, metadata)


def guardrail_result_from_diffs(
    diffs: SemanticDiff | Sequence[SemanticDiff],
    *,
    strict: bool,
    checked_files: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> GuardrailCheckResult:
    """Core's verdict over the diffs' violations; core counts the diffs as checked files."""

    items = [diffs] if isinstance(diffs, SemanticDiff) else list(diffs)
    # Core reads only each diff's ``guardrail_violations``, so only that field is marshalled.
    evidence = [
        {"guardrail_violations": [v.model_dump(mode="json") for v in diff.guardrail_violations]}
        for diff in items
    ]
    return _core_check_result({"diffs": evidence}, strict, checked_files, metadata)


def _core_check_result(
    evidence: dict[str, Any],
    strict: bool,
    checked_files: int | None,
    metadata: Mapping[str, Any] | None,
) -> GuardrailCheckResult:
    # Core owns the counts and the gate (``passed = !strict || no violations``, #296); the DTO
    # only validates the result and rejects counts that disagree with the violations.
    from intentumdiff.rust_core import _c_abi_call  # noqa: PLC0415

    request = {**evidence, "strict": strict, "checked_files": checked_files,
               "metadata": None if metadata is None else dict(metadata)}
    result = _c_abi_call("guardrail_check_result", json.dumps(request))
    return GuardrailCheckResult.model_validate(result)


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
    """Render a compact terminal summary for ``intentumdiff guardrails check``."""

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
                        "name": "IntentumDiff",
                        "informationUri": (
                            "https://buchochelliq-labs.github.io/intentumdiff-docs/"
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
    # Rust/C ABI positions are zero-based; SARIF and GitHub annotations are
    # one-based. Clamping collapses line/column 0 and 1 into the same value.
    return (
        position.start_line + 1,
        position.start_col + 1,
        position.end_line + 1,
        position.end_col + 1,
    )


def _display_location(violation: GuardrailViolation) -> str:
    if violation.position is None:
        return f"{violation.file}::{violation.semantic_path}"
    line, column, _, _ = _region(violation.position)
    return f"{violation.file}:{line}:{column}::{violation.semantic_path}"
