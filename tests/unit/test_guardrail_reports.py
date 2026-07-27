"""Unit tests for guardrail CI report rendering."""

from __future__ import annotations

import json

from intentdiff.analysis.guardrail_reports import (
    build_guardrail_check_result,
    render_guardrail_annotations,
    render_guardrail_sarif,
)
from intentdiff.core.models import (
    GuardrailSeverity,
    GuardrailViolation,
    NodePosition,
)


def _violation(
    *,
    severity: GuardrailSeverity = GuardrailSeverity.IMMUTABLE,
    rule_id: str = "prod-host",
    message: str = "Production host changed",
) -> GuardrailViolation:
    return GuardrailViolation(
        rule_id=rule_id,
        severity=severity,
        file="config.yaml",
        language="yaml",
        semantic_path="server.host",
        node_type="block_mapping_pair",
        old_node_id="old",
        new_node_id="new",
        position=NodePosition(
            start_line=3,
            start_col=2,
            end_line=3,
            end_col=20,
        ),
        old_value="localhost",
        new_value="prod.example.com",
        message=message,
    )


def test_guardrail_check_result_counts_and_serializes() -> None:
    result = build_guardrail_check_result(
        [
            _violation(),
            _violation(
                severity=GuardrailSeverity.IMPORTANT,
                rule_id="main-entry",
            ),
        ],
        checked_files=4,
        strict=True,
        metadata={"source": "unit"},
    )

    payload = json.loads(result.model_dump_json())

    assert result.violation_count == 2
    assert result.immutable_count == 1
    assert result.important_count == 1
    assert result.passed is False
    assert payload["metadata"] == {"source": "unit"}
    assert payload["violations"][0]["position"]["start_line"] == 3


def test_github_annotations_escape_special_fields() -> None:
    result = build_guardrail_check_result(
        [
            _violation(
                message="Host changed: prod, keep 100%\nnow\rplease",
            )
        ],
        checked_files=1,
        strict=True,
    )

    rendered = render_guardrail_annotations(result)

    assert rendered.startswith("::error ")
    assert "file=config.yaml" in rendered
    assert "line=3" in rendered
    assert "col=2" in rendered
    assert "Host changed: prod, keep 100%25%0Anow%0Dplease" in rendered
    assert "title=immutable guardrail prod-host" in rendered


def test_sarif_output_contains_rule_result_and_region() -> None:
    result = build_guardrail_check_result(
        [_violation()],
        checked_files=1,
        strict=True,
    )

    sarif = json.loads(render_guardrail_sarif(result))
    run = sarif["runs"][0]
    sarif_result = run["results"][0]
    location = sarif_result["locations"][0]["physicalLocation"]

    assert sarif["version"] == "2.1.0"
    assert run["tool"]["driver"]["name"] == "IntentDiff"
    assert run["tool"]["driver"]["rules"][0]["id"] == "prod-host"
    assert sarif_result["level"] == "error"
    assert sarif_result["ruleId"] == "prod-host"
    assert location["artifactLocation"]["uri"] == "config.yaml"
    assert location["region"]["startLine"] == 3
    assert location["region"]["startColumn"] == 2


def test_sarif_location_allows_missing_position() -> None:
    violation = _violation().model_copy(update={"position": None})
    result = build_guardrail_check_result(
        [violation],
        checked_files=1,
        strict=False,
    )

    sarif = json.loads(render_guardrail_sarif(result))
    location = sarif["runs"][0]["results"][0]["locations"][0]
    physical = location["physicalLocation"]

    assert physical["artifactLocation"]["uri"] == "config.yaml"
    assert "region" not in physical
