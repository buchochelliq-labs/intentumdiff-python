"""
tests/unit/test_git_source.py — unit tests for GitSource.

Each test that needs a real git history creates a temporary repository
with two commits so that HEAD~1 and HEAD refer to distinct file versions.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import git
import pytest

from intentdiff.sources.git_source import GitSource, _safe_file_path
from intentdiff.vcs.git_cli import NotAGitRepositoryError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> git.Repo:
    """Initialise a bare-minimum git repo with a configured identity."""
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test")
        cfg.set_value("user", "email", "test@example.com")
    return repo


def _commit_file(repo: git.Repo, rel_path: str, content: str, message: str) -> git.Commit:
    """Write *content* to *rel_path* inside the repo working tree and commit."""
    full = Path(repo.working_tree_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    repo.index.add([rel_path])
    return repo.index.commit(message)


# ---------------------------------------------------------------------------
# _safe_file_path
# ---------------------------------------------------------------------------


class TestSafeFilePath:
    def test_simple_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        assert _safe_file_path("src/foo.py") == "src/foo.py"

    def test_rejects_absolute(self, tmp_path):
        repo = _make_repo(tmp_path)
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_file_path("/etc/passwd")

    def test_rejects_dotdot(self, tmp_path):
        repo = _make_repo(tmp_path)
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_file_path("../secret.py")
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_file_path(r"..\secret.py")

    def test_rejects_dotdot_in_middle(self, tmp_path):
        repo = _make_repo(tmp_path)
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_file_path("a/../../etc/passwd")
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_file_path(r"a\..\secret.py")

    def test_rejects_windows_drive_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_file_path("C:/Windows/win.ini")


# ---------------------------------------------------------------------------
# GitSource.get_content
# ---------------------------------------------------------------------------


class TestGitSourceGetContent:
    def test_returns_old_and_new(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "app.py", "def foo(): pass\n", "v1")
        _commit_file(repo, "app.py", "def bar(): pass\n", "v2")

        src = GitSource(tmp_path, "app.py", old_ref="HEAD~1", new_ref="HEAD")
        old, new, fname, hint = src.get_content()

        assert "foo" in old
        assert "bar" in new
        assert fname == "app.py"
        assert hint is None

    def test_language_hint_passed_through(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "q.sql", "SELECT 1;\n", "v1")
        _commit_file(repo, "q.sql", "SELECT 2;\n", "v2")

        src = GitSource(tmp_path, "q.sql", old_ref="HEAD~1", new_ref="HEAD",
                        language_hint="sql")
        _, _, _, hint = src.get_content()
        assert hint == "sql"

    def test_filename_normalised(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "sub/mod.py", "x = 1\n", "v1")
        _commit_file(repo, "sub/mod.py", "x = 2\n", "v2")

        src = GitSource(tmp_path, "sub/mod.py")
        _, _, fname, _ = src.get_content()
        assert fname == "sub/mod.py"

    def test_explicit_commit_shas(self, tmp_path):
        repo = _make_repo(tmp_path)
        c1 = _commit_file(repo, "f.py", "a = 1\n", "first")
        c2 = _commit_file(repo, "f.py", "a = 2\n", "second")

        src = GitSource(tmp_path, "f.py", old_ref=c1.hexsha, new_ref=c2.hexsha)
        old, new, _, _ = src.get_content()
        assert "a = 1" in old
        assert "a = 2" in new

    def test_raises_for_bad_ref(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "x.py", "pass\n", "init")

        src = GitSource(tmp_path, "x.py", old_ref="nonexistent-branch", new_ref="HEAD")
        with pytest.raises(Exception):
            src.get_content()

    def test_raises_for_missing_file_in_commit(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "a.py", "pass\n", "v1")
        _commit_file(repo, "a.py", "pass\n", "v2")

        src = GitSource(tmp_path, "ghost.py")
        with pytest.raises(FileNotFoundError, match="not found in commit"):
            src.get_content()

    def test_invalid_repo_path_raises(self, tmp_path):
        """Passing a non-repo directory raises NotAGitRepositoryError."""
        empty_dir = tmp_path / "not_a_repo"
        empty_dir.mkdir()
        with pytest.raises(NotAGitRepositoryError):
            GitSource(empty_dir, "foo.py")

    def test_path_traversal_blocked_on_init(self, tmp_path):
        """GitSource.__init__ rejects '..' paths before hitting git."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "ok.py", "pass\n", "init")
        with pytest.raises(ValueError, match="Unsafe file path"):
            GitSource(tmp_path, "../outside.py")

    def test_search_parent_directories(self, tmp_path):
        """GitSource accepts a subdirectory of the repo as repo_path."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "pkg/mod.py", "v = 1\n", "v1")
        _commit_file(repo, "pkg/mod.py", "v = 2\n", "v2")

        subdir = tmp_path / "pkg"
        src = GitSource(subdir, "pkg/mod.py")
        old, new, _, _ = src.get_content()
        assert "v = 1" in old
        assert "v = 2" in new


# ---------------------------------------------------------------------------
# CLI parser — REPO argument is optional
# ---------------------------------------------------------------------------


class TestCliGitRepoOptional:
    """Test that 'intentdiff git FILE' works without an explicit REPO."""

    def test_repo_defaults_to_dot(self):
        from intentdiff.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["git", "src/app.py"])
        assert args.repo == "."
        assert args.file == "src/app.py"

    def test_repo_explicit_overrides_default(self):
        from intentdiff.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["git", "/some/repo", "src/app.py"])
        assert args.repo == "/some/repo"
        assert args.file == "src/app.py"

    def test_invalid_repo_prints_clear_error(self, tmp_path, capsys):
        """_cmd_git prints a helpful message when repo is not a git repo."""
        from intentdiff.cli import _build_parser, _cmd_git
        import argparse

        empty_dir = tmp_path / "not_a_repo"
        empty_dir.mkdir()

        parser = _build_parser()
        args = parser.parse_args(["git", str(empty_dir), "foo.py"])

        with pytest.raises(SystemExit) as exc_info:
            _cmd_git(args)
        assert exc_info.value.code == 1

    def test_cmd_git_uses_cwd_when_repo_is_dot(self, tmp_path, monkeypatch):
        """'git FILE' (no REPO) resolves against the current directory."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "app.py", "x = 1\n", "v1")
        _commit_file(repo, "app.py", "x = 2\n", "v2")

        monkeypatch.chdir(tmp_path)

        from intentdiff.cli import _build_parser, _cmd_git

        parser = _build_parser()
        args = parser.parse_args(["git", "app.py"])          # no REPO
        assert args.repo == "."

        # Capture stdout (terminal format) — just verify it runs without error
        captured = []
        monkeypatch.setattr(
            "intentdiff.cli._shared._render",
            lambda diff, fmt, output, fuel=None: captured.append(diff),
        )
        _cmd_git(args)
        assert len(captured) == 1
