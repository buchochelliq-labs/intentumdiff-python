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


def test_rename_into_policy_directory_disables_batch_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "unprotected" / "config.json"
    destination = tmp_path / "protected" / "config.json"
    source.parent.mkdir()
    destination.parent.mkdir()
    policy = destination.parent / "intentumdiff.yaml"
    policy.write_text(
        "guardrails:\n"
        "  protected:\n"
        "    - id: guard.version\n"
        "      language: json\n"
        "      path: version\n"
        "      severity: immutable\n"
        '      files: ["*.json"]\n',
        encoding="utf-8",
    )

    assert _find_policy_path(str(source), None) is None
    assert _find_policy_path(str(destination), None) == policy
    assert load_guardrail_policy(str(destination)).rules
    # A false value permits the certified Rust batch return to skip policy
    # application entirely for this move (differ.py, #258).
    assert guardrails_may_apply(str(source), str(destination), DiffConfig())


def test_rename_into_broken_destination_policy_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "unprotected" / "config.json"
    destination = tmp_path / "protected" / "config.json"
    source.parent.mkdir()
    destination.parent.mkdir()
    policy = destination.parent / "intentumdiff.yaml"
    try:
        policy.symlink_to(destination.parent / "missing-policy.yaml")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this host: {exc}")

    assert _find_policy_path(str(source), None) is None
    assert _find_policy_path(str(destination), None) == policy
    with pytest.raises(FileNotFoundError, match="intentumdiff.yaml"):
        guardrails_may_apply(str(source), str(destination), DiffConfig())


def test_move_without_policy_retains_optional_guardrails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old_path = tmp_path / "old" / "config.json"
    new_path = tmp_path / "new" / "config.json"
    old_path.parent.mkdir()
    new_path.parent.mkdir()
    assert not guardrails_may_apply(str(old_path), str(new_path), DiffConfig())
