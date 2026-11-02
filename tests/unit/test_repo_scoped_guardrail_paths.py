"""Git and live sources discover physical guardrails in their chosen repository.

Host filesystem path resolution is tested separately from the real Rust-owned
semantic guardrail integration. No semantic decision or mocked native engine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from intentumdiff.analysis.guardrails import _find_policy_path
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


@pytest.mark.parametrize("target_is_directory", [False, True])
def test_git_history_uses_committed_blobs_not_current_external_symlink(
    tmp_path: Path, monkeypatch, target_is_directory: bool
) -> None:
    """The checkout's current symlink must not veto a valid ref-to-ref review.

    This checks both a file symlink (containment failure in #270) and a
    directory symlink (policy discovery must not follow the final component).
    """
    repo, path = _repository(tmp_path)
    external = tmp_path / ("external-directory" if target_is_directory else "external-file")
    if target_is_directory:
        external.mkdir()
        (external / "intentumdiff.yaml").write_text(
            POLICY.replace("guard.version", "external.version"), encoding="utf-8"
        )
    else:
        external.write_text("unrelated external content", encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(external, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this host: {exc}")

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    source = GitSource(repo, "src/service.json", old_ref="HEAD~1", new_ref="HEAD")
    old_policy_path, new_policy_path = source.guardrail_policy_paths()
    assert old_policy_path == new_policy_path == str(path)
    # A directory symlink as the checkout's leaf must not redirect discovery.
    assert _find_policy_path(new_policy_path, None) == path.parent / "intentumdiff.yaml"

    # Real Rust parsing/finalization/guardrail evaluation on immutable Git blobs.
    # Neither source blob was changed by the uncommitted worktree symlink.
    result = SemanticDiffer().diff(source)
    assert result.new_filename == "src/service.json"
    assert any(v.rule_id == "guard.version" for v in result.guardrail_violations)
    assert all(v.rule_id != "external.version" for v in result.guardrail_violations)


def test_historical_git_without_repo_policy_ignores_unrelated_cwd_policy(
    tmp_path: Path, monkeypatch
) -> None:
    """An unrelated cwd policy must not govern a physical source in another repo."""
    repo, path = _repository(tmp_path)
    (path.parent / "intentumdiff.yaml").unlink()
    other = tmp_path / "unrelated-cwd"
    other.mkdir()
    foreign_policy = other / "intentumdiff.yaml"
    foreign_policy.write_text(
        POLICY.replace("guard.version", "foreign.version"), encoding="utf-8"
    )
    monkeypatch.chdir(other)

    source = GitSource(repo, "src/service.json", old_ref="HEAD~1", new_ref="HEAD")
    old_policy_path, new_policy_path = source.guardrail_policy_paths()
    assert old_policy_path == new_policy_path == str(path)
    assert _find_policy_path(new_policy_path, None) is None
    # Relative/synthetic callers still intentionally discover from cwd.
    assert _find_policy_path("local.json", None) == foreign_policy

    result = SemanticDiffer().diff(source)
    assert result.new_filename == "src/service.json"
    assert not result.guardrail_violations


def test_policy_resolution_failure_never_imports_foreign_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """An I/O error resolving a physical source must fail closed, not select cwd."""
    repo, path = _repository(tmp_path)
    (path.parent / "intentumdiff.yaml").unlink()
    foreign = tmp_path / "unrelated-cwd"
    foreign.mkdir()
    (foreign / "intentumdiff.yaml").write_text(POLICY, encoding="utf-8")
    monkeypatch.chdir(foreign)

    # Reproduce the filesystem error branch deterministically (e.g. a host
    # cannot resolve a repository parent) without requiring platform-specific
    # permissions. Previously this selected the unrelated cwd policy.
    original_resolve = Path.resolve

    def fail_selected_parent(self: Path, *args, **kwargs) -> Path:
        if self == path.parent:
            raise OSError("selected repository path unavailable")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_selected_parent)
    with pytest.raises(RuntimeError, match="Unable to resolve guardrail policy search path"):
        _find_policy_path(str(path), None)
