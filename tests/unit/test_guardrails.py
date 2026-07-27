"""Unit tests for protected config guardrails."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentdiff.analysis.diagnostics import DiagnosticsRecorder
from intentdiff.analysis.guardrails import (
    apply_guardrails_to_diff,
    load_guardrail_policy,
)
from intentdiff.core.models import (
    Change,
    ChangeType,
    DiffConfig,
    GuardrailSeverity,
    NodePosition,
    SemanticDiff,
    SemanticNode,
)
from intentdiff.differ import SemanticDiffer


def _pos(line: int = 0) -> NodePosition:
    return NodePosition(start_line=line, start_col=0, end_line=line, end_col=1)


def _node(
    node_id: str,
    node_type: str,
    label: str,
    children: list[SemanticNode] | None = None,
) -> SemanticNode:
    return SemanticNode(
        id=node_id,
        node_type=node_type,
        label=label,
        position=_pos(),
        structural_hash=f"h-{node_id}-{label}",
        children=children or [],
    )


def _write_policy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "intentdiff.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _diff(
    language: str,
    old_node: SemanticNode,
    new_node: SemanticNode,
    filename: str,
) -> SemanticDiff:
    return SemanticDiff(
        old_filename=filename,
        new_filename=filename,
        language=language,
        has_semantic_changes=True,
        changes=[
            Change(
                change_type=ChangeType.MODIFICATION,
                old_node=old_node,
                new_node=new_node,
                description="changed protected value",
            )
        ],
    )


def _yaml_server_tree(value: SemanticNode) -> SemanticNode:
    host = _node(
        "0.0.1.0",
        "block_mapping_pair",
        "host",
        [_node("k1", "plain_scalar", "host"), value],
    )
    server = _node(
        "0.0",
        "block_mapping_pair",
        "server",
        [
            _node("k0", "plain_scalar", "server"),
            _node("0.0.1", "block_node", "block_node", [host]),
        ],
    )
    return _node("0", "stream", "stream", [server])


def _json_main_tree(value: SemanticNode) -> SemanticNode:
    pair = _node(
        "0.0",
        "pair",
        "main",
        [_node("0.0.0", "string", "main"), value],
    )
    return _node("0", "document", "document", [_node("0.1", "object", "object", [pair])])


def _hcl_tree(ami: str) -> SemanticNode:
    attribute = _node(
        "0.0.0.0",
        "attribute",
        "ami",
        [
            _node("0.0.0.0.0", "identifier", "ami"),
            _node("0.0.0.0.1", "string_lit", ami),
        ],
    )
    body = _node("0.0.0", "body", "body", [attribute])
    block = _node("0.0", "block", "resource aws_instance web", [body])
    return _node("0", "config_file", "config_file", [block])


def _docker_tree(image: str) -> SemanticNode:
    return _node(
        "0",
        "source_file",
        "source_file",
        [_node("0.0", "from_instruction", f"FROM {image}")],
    )


def _puppet_tree(mode: str) -> SemanticNode:
    resource = _node(
        "0.0",
        "resource_statement",
        "file",
        [
            _node("0.0.0", "string", "/etc/app.conf"),
            _node("0.0.1", "attribute", "mode", [_node("0.0.1.0", "string", mode)]),
        ],
    )
    return _node("0", "source_file", "source_file", [resource])


def test_yaml_key_change_triggers_immutable_guardrail(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path,
        """
guardrails:
  protected:
    - id: server-host
      language: yaml
      path: server.host
      severity: immutable
      message: Server host changed
""",
    )
    old_value = _node("0.0.1.0.1", "plain_scalar", "localhost")
    new_value = _node("0.0.1.0.1", "plain_scalar", "prod.example.com")

    result = apply_guardrails_to_diff(
        _diff("yaml", old_value, new_value, "config.yaml"),
        old_tree=_yaml_server_tree(old_value),
        new_tree=_yaml_server_tree(new_value),
        old_source="",
        new_source="",
        config=DiffConfig(guardrail_policy_path=policy),
    )

    assert len(result.guardrail_violations) == 1
    violation = result.guardrail_violations[0]
    assert violation.rule_id == "server-host"
    assert violation.severity == GuardrailSeverity.IMMUTABLE
    assert violation.semantic_path == "server.host"
    assert violation.position == new_value.position


def test_json_key_change_triggers_important_guardrail(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path,
        """
guardrails:
  protected:
    - id: package-main
      language: json
      path: main
      severity: important
""",
    )
    old_value = _node("0.0.1", "string", "src/index.js")
    new_value = _node("0.0.1", "string", "dist/index.js")

    result = apply_guardrails_to_diff(
        _diff("json", old_value, new_value, "package.json"),
        old_tree=_json_main_tree(old_value),
        new_tree=_json_main_tree(new_value),
        old_source="",
        new_source="",
        config=DiffConfig(guardrail_policy_path=policy),
    )

    assert result.guardrail_violations[0].severity == GuardrailSeverity.IMPORTANT
    assert result.guardrail_violations[0].semantic_path == "main"
    assert result.guardrail_violations[0].position == new_value.position


def test_json_guardrail_runs_through_semantic_differ(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path,
        """
guardrails:
  protected:
    - id: package-main
      language: json
      path: main
      severity: immutable
""",
    )
    config = DiffConfig(guardrail_policy_path=policy)

    diff = SemanticDiffer(config).diff_strings(
        '{"name": "demo", "main": "src/index.js"}',
        '{"name": "demo", "main": "dist/index.js"}',
        "package.json",
        language_hint="json",
    )

    assert diff.guardrail_violations
    assert diff.guardrail_violations[0].rule_id == "package-main"
    assert diff.guardrail_violations[0].semantic_path == "main"


@pytest.mark.parametrize(
    ("language", "filename", "path", "old_root", "new_root", "old_node", "new_node"),
    [
        (
            "hcl",
            "main.tf",
            "resource/aws_instance/web.ami",
            _hcl_tree("ami-1"),
            _hcl_tree("ami-2"),
            _node("0.0.0.0.1", "string_lit", "ami-1"),
            _node("0.0.0.0.1", "string_lit", "ami-2"),
        ),
        (
            "dockerfile",
            "Dockerfile",
            "instruction.from.0",
            _docker_tree("python:3.11"),
            _docker_tree("python:3.12"),
            _node("0.0", "from_instruction", "FROM python:3.11"),
            _node("0.0", "from_instruction", "FROM python:3.12"),
        ),
        (
            "puppet",
            "site.pp",
            "resource.file./etc/app.conf.attribute.mode",
            _puppet_tree("0644"),
            _puppet_tree("0600"),
            _node("0.0.1.0", "string", "0644"),
            _node("0.0.1.0", "string", "0600"),
        ),
    ],
)
def test_resource_profile_guardrails_trigger(
    tmp_path: Path,
    language: str,
    filename: str,
    path: str,
    old_root: SemanticNode,
    new_root: SemanticNode,
    old_node: SemanticNode,
    new_node: SemanticNode,
) -> None:
    policy = _write_policy(
        tmp_path,
        f"""
guardrails:
  protected:
    - id: protected-resource
      language: {language}
      path: {path}
      severity: immutable
""",
    )

    result = apply_guardrails_to_diff(
        _diff(language, old_node, new_node, filename),
        old_tree=old_root,
        new_tree=new_root,
        old_source="",
        new_source="",
        config=DiffConfig(guardrail_policy_path=policy),
    )

    assert result.guardrail_violations
    assert result.guardrail_violations[0].semantic_path == path
    assert result.guardrail_violations[0].position == new_node.position


def test_code_file_guardrail_rule_is_rejected(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path,
        """
guardrails:
  protected:
    - id: code-rule
      language: python
      path: important_function
      severity: immutable
""",
    )

    with pytest.raises(ValueError, match="unsupported language"):
        load_guardrail_policy("code.py", explicit_path=policy)


def test_policy_file_is_protected_by_default() -> None:
    result = apply_guardrails_to_diff(
        SemanticDiff.style_only("intentdiff.yaml", "intentdiff.yaml", "yaml"),
        old_tree=None,
        new_tree=None,
        old_source="guardrails: {}\n",
        new_source="guardrails:\n  protected: []\n",
        config=DiffConfig(),
    )

    assert len(result.guardrail_violations) == 1
    assert result.guardrail_violations[0].rule_id == "intentdiff.policy_file"
    assert result.guardrail_violations[0].severity == GuardrailSeverity.IMMUTABLE
    assert result.guardrail_violations[0].position is None


def test_guardrail_violation_is_recorded_in_diagnostics(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path,
        """
guardrails:
  protected:
    - id: package-main
      language: json
      path: main
      severity: immutable
""",
    )
    old_value = _node("0.0.1", "string", "src/index.js")
    new_value = _node("0.0.1", "string", "dist/index.js")
    diagnostics = DiagnosticsRecorder(enabled=True, max_events=20)

    apply_guardrails_to_diff(
        _diff("json", old_value, new_value, "package.json"),
        old_tree=_json_main_tree(old_value),
        new_tree=_json_main_tree(new_value),
        old_source="",
        new_source="",
        config=DiffConfig(guardrail_policy_path=policy),
        diagnostics=diagnostics,
    )

    assert any(event["stage"] == "guardrails" for event in diagnostics.snapshot()["events"])
