"""
tests/unit/test_working_tree_source.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for WorkingTreeSource and GitSource.working_tree().
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from intentdiff.sources.git_source import GitSource, WorkingTreeSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test")
        cfg.set_value("user", "email", "test@example.com")
    return repo


def _norm(s: str) -> str:
    """Normalize CRLF to LF for cross-platform string comparison."""
    return s.replace("\r\n", "\n")


def _commit_file(repo: git.Repo, rel_path: str, content: str, message: str) -> git.Commit:
    full = Path(repo.working_tree_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    repo.index.add([rel_path])
    return repo.index.commit(message)


# ---------------------------------------------------------------------------
# WorkingTreeSource
# ---------------------------------------------------------------------------


class TestWorkingTreeSource:
    def test_returns_committed_vs_working_tree(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "foo.py", "x = 1\n", "init")

        # Modify on disk without committing
        disk_path = Path(repo.working_tree_dir) / "foo.py"
        disk_path.write_text("x = 2\n", encoding="utf-8")

        src = WorkingTreeSource(tmp_path, "foo.py")
        old, new, filename, lang = src.get_content()

        assert _norm(old) == "x = 1\n"
        assert _norm(new) == "x = 2\n"
        assert filename == "foo.py"
        assert lang is None

    def test_language_hint_propagated(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "q.sql", "SELECT 1;\n", "init")

        src = WorkingTreeSource(tmp_path, "q.sql", language_hint="sql")
        _, _, _, lang = src.get_content()
        assert lang == "sql"

    def test_custom_ref(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "foo.py", "v = 'old'\n", "first commit")
        _commit_file(repo, "foo.py", "v = 'committed'\n", "second commit")

        disk_path = Path(repo.working_tree_dir) / "foo.py"
        disk_path.write_text("v = 'working'\n", encoding="utf-8")

        src = WorkingTreeSource(tmp_path, "foo.py", ref="HEAD~1")
        old, new, _, _ = src.get_content()
        assert _norm(old) == "v = 'old'\n"
        assert _norm(new) == "v = 'working'\n"

    def test_file_not_in_working_tree_raises(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "exists.py", "pass\n", "init")

        # Delete the file from disk but keep it in git
        (Path(repo.working_tree_dir) / "exists.py").unlink()

        src = WorkingTreeSource(tmp_path, "exists.py")
        with pytest.raises(FileNotFoundError):
            src.get_content()

    def test_search_parent_directories(self, tmp_path):
        """WorkingTreeSource accepts a subdirectory path as repo_path."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "sub/bar.py", "a = 0\n", "init")

        subdir = tmp_path / "sub"
        subdir.mkdir(exist_ok=True)
        (subdir / "bar.py").write_text("a = 1\n", encoding="utf-8")

        src = WorkingTreeSource(subdir, "sub/bar.py")
        old, new, _, _ = src.get_content()
        assert _norm(old) == "a = 0\n"
        assert _norm(new) == "a = 1\n"


# ---------------------------------------------------------------------------
# GitSource.working_tree() factory
# ---------------------------------------------------------------------------


class TestGitSourceWorkingTreeFactory:
    def test_returns_working_tree_source_instance(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "f.py", "pass\n", "init")

        src = GitSource.working_tree(tmp_path, "f.py")
        assert isinstance(src, WorkingTreeSource)

    def test_factory_default_ref_is_head(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "f.py", "old\n", "init")

        disk_path = Path(repo.working_tree_dir) / "f.py"
        disk_path.write_text("new\n", encoding="utf-8")

        src = GitSource.working_tree(tmp_path, "f.py")
        old, new, _, _ = src.get_content()
        assert _norm(old) == "old\n"
        assert _norm(new) == "new\n"

    def test_factory_custom_ref(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "f.py", "v1\n", "c1")
        _commit_file(repo, "f.py", "v2\n", "c2")

        disk_path = Path(repo.working_tree_dir) / "f.py"
        disk_path.write_text("v3\n", encoding="utf-8")

        src = GitSource.working_tree(tmp_path, "f.py", ref="HEAD~1")
        old, new, _, _ = src.get_content()
        assert _norm(old) == "v1\n"
        assert _norm(new) == "v3\n"

    def test_factory_language_hint(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "schema.sql", "SELECT 1;\n", "init")

        src = GitSource.working_tree(tmp_path, "schema.sql", language_hint="sql")
        _, _, _, lang = src.get_content()
        assert lang == "sql"
