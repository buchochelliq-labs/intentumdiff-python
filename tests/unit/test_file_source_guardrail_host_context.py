"""FileSource must discover destination guardrails without changing display labels (#259)."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentumdiff.differ import SemanticDiffer
from intentumdiff.sources.file_source import FileSource


OLD_JSON = '{\n  "version": 1,\n  "name": "x"\n}\n'
NEW_JSON = '{\n  "version": 2,\n  "name": "x"\n}\n'
POLICY = (
    "guardrails:\n"
    "  protected:\n"
    "    - id: guard.version\n"
    "      language: json\n"
    "      path: version\n"
    "      severity: immutable\n"
    '      files: ["*.json"]\n'
)


def _files(tmp_path: Path) -> tuple[Path, Path]:
    old = tmp_path / "before" / "config.json"
    new = tmp_path / "protected" / "config.json"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_text(OLD_JSON, encoding="utf-8")
    new.write_text(NEW_JSON, encoding="utf-8")
    return old, new


def test_file_source_destination_rule_runs_in_rust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    (new.parent / "intentumdiff.yaml").write_text(POLICY, encoding="utf-8")

    diff = SemanticDiffer().diff(FileSource(old, new))
    assert any(v.rule_id == "guard.version" for v in diff.guardrail_violations)
    # Policy discovery uses the physical new path; public labels stay stable.
    assert diff.old_filename == "config.json"
    assert diff.new_filename == "config.json"


def test_file_source_unreadable_destination_policy_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    policy = new.parent / "intentumdiff.yaml"
    policy.write_text(POLICY, encoding="utf-8")
    original = Path.read_text

    def unreadable(self: Path, *args: object, **kwargs: object) -> str:
        if self == policy:
            raise FileNotFoundError("destination guardrail target cannot be read")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(FileNotFoundError, match="destination guardrail target"):
        SemanticDiffer().diff(FileSource(old, new))


def test_file_source_no_policy_does_not_invent_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    diff = SemanticDiffer().diff(FileSource(old, new))
    assert not diff.guardrail_violations
    assert diff.old_filename == diff.new_filename == "config.json"
