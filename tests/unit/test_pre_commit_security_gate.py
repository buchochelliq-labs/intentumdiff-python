from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_pre_commit_gate():
    script = Path(__file__).resolve().parents[2] / "scripts" / "pre_commit_security_gate.py"
    spec = importlib.util.spec_from_file_location("pre_commit_security_gate", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_source_plans_ruff_compile_and_security_tests() -> None:
    gate = _load_pre_commit_gate()

    plan = gate.plan_for_paths([
        "src/intentdiff/live_server.py",
        "docs/README.md",
    ])

    action_ids = {action.id for action in plan.blocking_actions}
    assert {"ruff-security", "python-compile", "security-tests"} <= action_ids


def test_docs_only_change_does_not_block_commit_with_expensive_checks() -> None:
    gate = _load_pre_commit_gate()

    plan = gate.plan_for_paths(["docs/README.md"])

    assert plan.blocking_actions == ()
    assert plan.advisory_actions == ()


def test_dependency_manifest_plans_security_tests_and_pip_audit() -> None:
    gate = _load_pre_commit_gate()

    plan = gate.plan_for_paths(["pyproject.toml"])

    assert any(action.id == "security-tests" for action in plan.blocking_actions)
    assert [action.id for action in plan.advisory_actions] == ["pip-audit"]


def test_vscode_manifest_plans_tests_and_npm_audit() -> None:
    gate = _load_pre_commit_gate()

    plan = gate.plan_for_paths(["plugins/vscode/package.json"])

    assert [action.id for action in plan.blocking_actions] == ["vscode-tests"]
    assert [action.id for action in plan.advisory_actions] == ["npm-audit"]


def test_advisory_detection_distinguishes_vulnerabilities_from_setup_failures() -> None:
    gate = _load_pre_commit_gate()

    assert gate.advisory_output_reports_vulnerability(
        "Found 1 known vulnerability in 1 package"
    )
    assert gate.advisory_output_reports_vulnerability(
        "5 moderate vulnerabilities\nRun npm audit fix"
    )
    assert not gate.advisory_output_reports_vulnerability(
        "Failed to upgrade pip in a temporary environment"
    )
