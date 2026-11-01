"""Auto-discovered guardrail policies must fail closed when a path entry is broken."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentumdiff.analysis.guardrails import (
    _find_policy_path,
    guardrails_may_apply,
    load_guardrail_policy,
)
from intentumdiff.core.models import DiffConfig


def _source_file(tmp_path: Path) -> Path:
    source = tmp_path / "config.yaml"
    source.write_text("api_key: before\n", encoding="utf-8")
    return source


def test_absent_auto_discovered_policy_remains_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source_file(tmp_path)

    assert _find_policy_path(str(source), None) is None
    assert load_guardrail_policy(str(source)).rules == ()
    assert not guardrails_may_apply(str(source), None, DiffConfig())


def test_broken_auto_discovered_policy_symlink_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source_file(tmp_path)
    policy = tmp_path / "intentumdiff.yaml"
    try:
        policy.symlink_to(tmp_path / "missing-policy.yaml")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this host: {exc}")

    # Path.exists() is false for a dangling link even though the policy entry
    # exists. A no-policy result would suppress the Rust guardrail engine.
    assert policy.is_symlink()
    assert not policy.exists()
    assert _find_policy_path(str(source), None) == policy

    with pytest.raises(FileNotFoundError, match="intentumdiff.yaml"):
        load_guardrail_policy(str(source))
    with pytest.raises(FileNotFoundError, match="intentumdiff.yaml"):
        guardrails_may_apply(str(source), None, DiffConfig())
