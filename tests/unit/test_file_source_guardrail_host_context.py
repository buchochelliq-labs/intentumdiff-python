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


def test_file_source_alias_cannot_bypass_physical_filename_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    # Exact filename scope: a display alias must not make the physical
    # protected/config.json disappear from Rust's filename check (#260).
    policy = POLICY.replace('files: ["*.json"]', 'files: ["config.json"]')
    (new.parent / "intentumdiff.yaml").write_text(policy, encoding="utf-8")
    diff = SemanticDiffer().diff(FileSource(old, new, filename="alias.json"))
    assert any(v.rule_id == "guard.version" for v in diff.guardrail_violations)
    assert diff.old_filename == diff.new_filename == "alias.json"
    assert all(v.file == "alias.json" for v in diff.guardrail_violations)


def test_file_source_alias_cannot_hide_immutable_policy_file_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old = tmp_path / "before" / "intentumdiff.yaml"
    new = tmp_path / "protected" / "intentumdiff.yaml"
    old.parent.mkdir()
    new.parent.mkdir()
    # These are valid policy documents with no protected rules. Rust must
    # nevertheless flag the policy file itself as immutable when it changes.
    old.write_text("guardrails:\n  protected: []\n", encoding="utf-8")
    new.write_text("guardrails:\n  protected: null\n", encoding="utf-8")
    diff = SemanticDiffer().diff(FileSource(old, new, filename="alias.yaml"))
    assert any(v.rule_id == "intentumdiff.policy_file" and v.severity.value == "immutable"
               for v in diff.guardrail_violations)
    assert diff.old_filename == diff.new_filename == "alias.yaml"
    assert all(v.file == "alias.yaml" for v in diff.guardrail_violations)


def test_file_source_move_out_preserves_source_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    (old.parent / "intentumdiff.yaml").write_text(POLICY, encoding="utf-8")
    # Destination has no policy; the source's immutable rule still protects
    # version across the move. A display alias cannot hide the source (#262).
    diff = SemanticDiffer().diff(FileSource(old, new, filename="public-alias.json"))
    assert any(v.rule_id == "guard.version" for v in diff.guardrail_violations)
    assert diff.old_filename == diff.new_filename == "public-alias.json"
    assert all(v.file == "public-alias.json" for v in diff.guardrail_violations)


def test_file_source_move_between_distinct_policy_directories_uses_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    (old.parent / "intentumdiff.yaml").write_text(
        POLICY.replace("guard.version", "guard.source"), encoding="utf-8"
    )
    (new.parent / "intentumdiff.yaml").write_text(
        POLICY.replace("guard.version", "guard.destination"), encoding="utf-8"
    )
    diff = SemanticDiffer().diff(FileSource(old, new))
    ids = {v.rule_id for v in diff.guardrail_violations}
    assert {"guard.source", "guard.destination"} <= ids
    assert diff.old_filename == diff.new_filename == "config.json"


def test_file_source_unreadable_source_policy_fails_closed_on_move_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _files(tmp_path)
    policy = old.parent / "intentumdiff.yaml"
    policy.write_text(POLICY, encoding="utf-8")
    original = Path.read_text

    def unreadable(self: Path, *args: object, **kwargs: object) -> str:
        if self == policy:
            raise OSError("source guardrail policy cannot be read")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(OSError, match="source guardrail policy cannot be read"):
        SemanticDiffer().diff(FileSource(old, new))
