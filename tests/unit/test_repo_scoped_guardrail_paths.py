"""Git and live sources discover physical guardrails in their chosen repository.

Host filesystem path resolution is tested separately from the real Rust-owned
semantic guardrail integration. No semantic decision or mocked native engine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from intentumdiff.differ import SemanticDiffer
from intentumdiff.sources.git_source import GitSource, WorkingTreeSource
from intentumdiff.sources.live_buffer_source import LiveBufferSource


OLD = '{"version":1,"name":"service"}\n'
NEW = '{"version":2,"name":"service"}\n'
POLICY = (
    "guardrails:\n"
    "  protected:\n"
    "    - id: guard.version\n"
    "      language: json\n"
    "      path: version\n"
    "      severity: immutable\n"
    '      files: ["*.json"]\n'
)


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "selected-repository"
    path = repo / "src" / "service.json"
    path.parent.mkdir(parents=True)
    _git(repo, "init", "--quiet")
    path.write_text(OLD, encoding="utf-8")
    _git(repo, "add", "src/service.json")
    _git(repo, "-c", "user.name=IntentumDiff", "-c",
         "user.email=tests@example.invalid", "commit", "--quiet", "-m", "baseline")
    path.write_text(NEW, encoding="utf-8")
    _git(repo, "add", "src/service.json")
    _git(repo, "-c", "user.name=IntentumDiff", "-c",
         "user.email=tests@example.invalid", "commit", "--quiet", "-m", "version bump")
    (path.parent / "intentumdiff.yaml").write_text(POLICY, encoding="utf-8")
    return repo, path


def _sources(repo: Path) -> tuple[GitSource, WorkingTreeSource, LiveBufferSource]:
    inside = repo / "src"
    return (
        GitSource(inside, "src/service.json", old_ref="HEAD~1", new_ref="HEAD"),
        WorkingTreeSource(inside, "src/service.json", ref="HEAD~1"),
        LiveBufferSource(inside, "src/service.json", NEW, ref="HEAD~1"),
    )


def test_physical_repo_policy_paths_are_independent_of_process_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    repo, path = _repository(tmp_path)
    outside = tmp_path / "unrelated-cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)

    for source in _sources(repo):
        old_path, new_path = source.guardrail_policy_paths()
        assert old_path == new_path == str(path.resolve())
        assert Path(new_path).parent / "intentumdiff.yaml" == path.parent / "intentumdiff.yaml"


def test_real_rust_guardrail_enforces_subdirectory_policy_outside_repo_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    repo, _ = _repository(tmp_path)
    outside = tmp_path / "unrelated-cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)

    for source in _sources(repo):
        diff = SemanticDiffer().diff(source)
        assert any(
            violation.rule_id == "guard.version"
            for violation in diff.guardrail_violations
        ), f"Missing guardrail in {type(source).__name__}"
        expected_label = (
            "service.json" if isinstance(source, LiveBufferSource)
            else "src/service.json"
        )
        assert diff.new_filename == expected_label
