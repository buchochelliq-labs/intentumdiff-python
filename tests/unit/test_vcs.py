"""
tests/unit/test_vcs.py — unit tests for the VCS abstraction layer.

Tests cover:
  - ``ChangedFile`` dataclass invariants
  - ``VcsBackend`` abstract interface
  - ``GitVcsBackend`` against a fixture git repo (delegates to the Rust vcs_backend)
  - ``SvnVcsBackend`` / ``HgVcsBackend`` thin-wrapper delegation to the Rust vcs_backend
    (the ``rust_core`` boundary is mocked; the CLIs + parsing are cargo-tested in the core)
  - ``PerforceVcsBackend`` import-time guard (p4python not available)
  - ref-injection guards enforced by the core (hg/svn/p4)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import git
import pytest

from intentdiff.vcs.base import ChangedFile, VcsBackend
from intentdiff.vcs.git_backend import GitVcsBackend, _safe_path
from intentdiff.vcs.hg_backend import HgVcsBackend
from intentdiff.vcs.perforce_backend import PerforceVcsBackend
from intentdiff.vcs.svn_backend import SvnVcsBackend

# ===========================================================================
# Helpers
# ===========================================================================


def _make_git_repo(tmp_path: Path) -> git.Repo:
    """Create a minimal git repo with two commits for testing."""
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    # Commit 1: add foo.py
    foo = tmp_path / "foo.py"
    foo.write_text("def hello():\n    pass\n", encoding="utf-8")
    repo.index.add(["foo.py"])
    repo.index.commit("initial commit")

    # Commit 2: modify foo.py and add bar.py
    foo.write_text("def hello():\n    return 42\n", encoding="utf-8")
    bar = tmp_path / "bar.py"
    bar.write_text("x = 1\n", encoding="utf-8")
    repo.index.add(["foo.py", "bar.py"])
    repo.index.commit("second commit")

    return repo


# ===========================================================================
# ChangedFile dataclass
# ===========================================================================


class TestChangedFile:
    def test_basic_construction(self) -> None:
        cf = ChangedFile(old_path="a.py", new_path="a.py", change_type="modified")
        assert cf.old_path == "a.py"
        assert cf.new_path == "a.py"
        assert cf.change_type == "modified"
        assert cf.is_binary is False

    def test_frozen(self) -> None:
        cf = ChangedFile(old_path="a.py", new_path="a.py", change_type="modified")
        with pytest.raises((AttributeError, TypeError)):
            cf.is_binary = True  # type: ignore[misc]

    def test_added_file(self) -> None:
        cf = ChangedFile(old_path=None, new_path="new.py", change_type="added")
        assert cf.old_path is None
        assert cf.new_path == "new.py"

    def test_deleted_file(self) -> None:
        cf = ChangedFile(old_path="gone.py", new_path=None, change_type="deleted")
        assert cf.new_path is None

    def test_renamed_file(self) -> None:
        cf = ChangedFile(old_path="old.py", new_path="new.py", change_type="renamed")
        assert cf.change_type == "renamed"

    def test_binary_flag(self) -> None:
        cf = ChangedFile(
            old_path="img.png", new_path="img.png",
            change_type="modified", is_binary=True
        )
        assert cf.is_binary is True

    def test_equality(self) -> None:
        cf1 = ChangedFile(old_path="a.py", new_path="b.py", change_type="renamed")
        cf2 = ChangedFile(old_path="a.py", new_path="b.py", change_type="renamed")
        assert cf1 == cf2


# ===========================================================================
# VcsBackend abstract interface
# ===========================================================================


class TestVcsBackendInterface:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            VcsBackend()  # type: ignore[abstract]

    def test_merge_stubs_raise_not_implemented(self) -> None:
        """Non-abstract merge hooks should raise NotImplementedError by default."""

        class ConcreteBackend(VcsBackend):
            def get_blob(self, path: str, ref: str) -> str:
                return ""

            def list_changed_files(self, ref_a: str, ref_b: str) -> list[ChangedFile]:
                return []

            def get_working_file(self, path: str) -> str:
                return ""

            def list_working_tree_changes(self, ref: str = "HEAD") -> list[ChangedFile]:
                return []

            def resolve_root(self) -> Path:
                return Path(".")

        backend = ConcreteBackend()
        with pytest.raises(NotImplementedError):
            backend.get_merge_base("a", "b")
        with pytest.raises(NotImplementedError):
            backend.get_conflict_content("file.py")


# ===========================================================================
# GitVcsBackend
# ===========================================================================


class TestGitVcsBackend:
    def test_resolve_root(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        assert backend.resolve_root() == tmp_path

    def test_get_blob_at_head(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        content = backend.get_blob("foo.py", "HEAD")
        assert "return 42" in content

    def test_get_blob_at_first_commit(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        first_sha = list(repo.iter_commits())[-1].hexsha
        backend = GitVcsBackend(tmp_path)
        content = backend.get_blob("foo.py", first_sha)
        assert "pass" in content
        assert "return 42" not in content

    def test_get_blob_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        content = backend.get_blob("does_not_exist.py", "HEAD")
        assert content == ""

    def test_list_changed_files_between_commits(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        commits = list(repo.iter_commits())
        new_sha = commits[0].hexsha
        old_sha = commits[1].hexsha
        backend = GitVcsBackend(tmp_path)
        changes = backend.list_changed_files(old_sha, new_sha)
        paths = {cf.new_path or cf.old_path for cf in changes}
        assert "foo.py" in paths
        assert "bar.py" in paths

    def test_list_changed_files_added_has_no_old_path(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        commits = list(repo.iter_commits())
        new_sha = commits[0].hexsha
        old_sha = commits[1].hexsha
        backend = GitVcsBackend(tmp_path)
        changes = backend.list_changed_files(old_sha, new_sha)
        added = [cf for cf in changes if cf.change_type == "added"]
        assert any(cf.old_path is None for cf in added)

    def test_get_working_file(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        content = backend.get_working_file("foo.py")
        assert "return 42" in content

    def test_get_working_file_missing_returns_empty(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        assert backend.get_working_file("no_such_file.py") == ""

    def test_list_working_tree_changes_clean(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        # No uncommitted changes — list should be empty
        changes = backend.list_working_tree_changes("HEAD")
        assert changes == []

    def test_list_working_tree_changes_detects_modification(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        backend = GitVcsBackend(tmp_path)
        (tmp_path / "foo.py").write_text("def hello():\n    return 99\n")
        changes = backend.list_working_tree_changes("HEAD")
        assert any("foo.py" in (cf.new_path or cf.old_path or "") for cf in changes)

    def test_get_merge_base(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        commits = list(repo.iter_commits())
        backend = GitVcsBackend(tmp_path)
        base = backend.get_merge_base(commits[0].hexsha, commits[1].hexsha)
        # Merge base of two sequential commits is the older one
        assert base == commits[1].hexsha


class TestSafePath:
    def test_valid_path(self) -> None:
        assert _safe_path("src/foo.py") == "src/foo.py"

    def test_traversal_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_path("../secret.py")
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_path(r"..\secret.py")
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_path(r"a\..\secret.py")

    def test_absolute_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_path("/etc/passwd")
        with pytest.raises(ValueError, match="Unsafe file path"):
            _safe_path("C:/Windows/win.ini")


# ===========================================================================
# SvnVcsBackend — thin wrapper over the Rust vcs_backend (svn dialect)
# ===========================================================================


class TestSvnVcsBackend:
    """SvnVcsBackend marshals to/from the Rust ``vcs_backend`` (svn dialect). The svn CLI +
    ``svn --xml`` parsing live in the core (cargo-tested); here we mock the ``rust_core``
    boundary and assert delegation, DTO marshalling, and repo_url pass-through."""

    def test_get_blob_delegates_with_repo_url(self) -> None:
        backend = SvnVcsBackend("/wc", repo_url="svn://example.com/repo")
        with patch(
            "intentdiff.rust_core.vcs_backend_get_blob", return_value="print('hello')\n"
        ) as m:
            result = backend.get_blob("src/foo.py", "42")
        assert "hello" in result
        args = m.call_args.args
        # (vcs, root, path, ref, repo_url) — repo_url forwarded to the core.
        assert args[0] == "svn" and args[3] == "42"
        assert args[4] == "svn://example.com/repo"

    def test_get_blob_without_repo_url_passes_none(self) -> None:
        backend = SvnVcsBackend("/wc")
        with patch("intentdiff.rust_core.vcs_backend_get_blob", return_value="") as m:
            backend.get_blob("missing.py", "HEAD")
        assert m.call_args.args[4] is None

    def test_list_changed_files_marshals_dto(self) -> None:
        backend = SvnVcsBackend("/wc")
        rows = [
            {"old_path": "src/foo.py", "new_path": "src/foo.py",
             "change_type": "modified", "is_binary": False},
        ]
        with patch(
            "intentdiff.rust_core.vcs_backend_changed_files", return_value=rows
        ) as m:
            result = backend.list_changed_files("10", "20")
        assert result[0].change_type == "modified"
        assert m.call_args.args == ("svn", "/wc", "10", "20", None)

    def test_list_working_tree_changes_marshals_dto(self) -> None:
        backend = SvnVcsBackend("/wc")
        rows = [
            {"old_path": None, "new_path": "new.py",
             "change_type": "added", "is_binary": False},
        ]
        with patch(
            "intentdiff.rust_core.vcs_backend_working_tree_changes", return_value=rows
        ):
            result = backend.list_working_tree_changes()
        assert result[0].change_type == "added"
        assert result[0].old_path is None

    def test_resolve_root_delegates(self) -> None:
        backend = SvnVcsBackend("/wc")
        with patch(
            "intentdiff.rust_core.vcs_backend_resolve_root", return_value="/srv/svn/wc"
        ) as m:
            root = backend.resolve_root()
        assert root == Path("/srv/svn/wc")
        m.assert_called_once_with("svn", "/wc")

    def test_get_working_file(self, tmp_path: Path) -> None:
        (tmp_path / "foo.py").write_text("hello\n")
        backend = SvnVcsBackend(tmp_path)
        assert "hello" in backend.get_working_file("foo.py")


# ===========================================================================
# HgVcsBackend — thin wrapper over the Rust vcs_backend (hg dialect)
# ===========================================================================


class TestHgVcsBackend:
    """HgVcsBackend is a thin wrapper: it marshals to/from the Rust ``vcs_backend`` (hg
    dialect). The hg CLI + status parsing live in the core (cargo-tested); here we mock the
    ``rust_core`` boundary and assert the delegation + DTO marshalling."""

    @staticmethod
    def _backend(root: str = "/repo") -> HgVcsBackend:
        with patch("intentdiff.rust_core.vcs_backend_resolve_root", return_value=root):
            return HgVcsBackend(root)

    def test_init_resolves_root_via_core(self) -> None:
        with patch(
            "intentdiff.rust_core.vcs_backend_resolve_root", return_value="/hg/root"
        ) as m:
            backend = HgVcsBackend("/anywhere/inside")
        m.assert_called_once_with("hg", "/anywhere/inside")
        assert backend.resolve_root() == Path("/hg/root")

    def test_get_blob_delegates(self) -> None:
        backend = self._backend()
        with patch(
            "intentdiff.rust_core.vcs_backend_get_blob", return_value="x = 1\n"
        ) as m:
            result = backend.get_blob("src/foo.py", "tip")
        assert "x = 1" in result
        m.assert_called_once_with("hg", "/repo", "src/foo.py", "tip")

    def test_list_changed_files_marshals_dto(self) -> None:
        backend = self._backend()
        rows = [
            {"old_path": "src/foo.py", "new_path": "src/foo.py",
             "change_type": "modified", "is_binary": False},
            {"old_path": None, "new_path": "src/bar.py",
             "change_type": "added", "is_binary": False},
        ]
        with patch("intentdiff.rust_core.vcs_backend_changed_files", return_value=rows):
            result = backend.list_changed_files("0", "1")
        assert [type(cf) for cf in result] == [ChangedFile, ChangedFile]
        assert {cf.change_type for cf in result} == {"modified", "added"}
        added = next(cf for cf in result if cf.change_type == "added")
        assert added.old_path is None and added.new_path == "src/bar.py"

    def test_list_working_tree_changes_marshals_dto(self) -> None:
        backend = self._backend()
        rows = [
            {"old_path": "a.py", "new_path": "a.py",
             "change_type": "modified", "is_binary": False},
        ]
        with patch(
            "intentdiff.rust_core.vcs_backend_working_tree_changes", return_value=rows
        ) as m:
            result = backend.list_working_tree_changes()
        assert result[0].change_type == "modified"
        m.assert_called_once_with("hg", "/repo", ".")

    def test_get_working_file(self, tmp_path: Path) -> None:
        (tmp_path / "foo.py").write_text("hello\n")
        backend = self._backend(str(tmp_path))
        assert "hello" in backend.get_working_file("foo.py")

    def test_get_working_file_missing(self, tmp_path: Path) -> None:
        backend = self._backend(str(tmp_path))
        assert backend.get_working_file("no_such.py") == ""


# ===========================================================================
# PerforceVcsBackend — thin wrapper over the Rust vcs_backend (p4 dialect)
# ===========================================================================


class TestPerforceVcsBackend:
    """PerforceVcsBackend marshals to/from the Rust ``vcs_backend`` (p4 dialect). The ``p4``
    CLI + ``-ztag`` parsing live in the core (cargo-tested); here we mock the ``rust_core``
    boundary. p4python is no longer used — the backend shells the CLI via the core."""

    @staticmethod
    def _backend(root: str = "/ws") -> PerforceVcsBackend:
        with patch("intentdiff.rust_core.vcs_backend_resolve_root", return_value=root):
            return PerforceVcsBackend("/ws")

    def test_init_resolves_client_root_via_core(self) -> None:
        with patch(
            "intentdiff.rust_core.vcs_backend_resolve_root", return_value="/client"
        ) as m:
            backend = PerforceVcsBackend("/ws/sub")
        m.assert_called_once_with("p4", "/ws/sub")
        assert backend.resolve_root() == Path("/client")

    def test_explicit_client_root_skips_core(self) -> None:
        # A supplied client_root is used verbatim; the core is not consulted.
        backend = PerforceVcsBackend("/ws", client_root="/explicit")
        assert backend.resolve_root() == Path("/explicit")

    def test_get_blob_delegates(self) -> None:
        backend = self._backend()
        with patch(
            "intentdiff.rust_core.vcs_backend_get_blob", return_value="x = 1\n"
        ) as m:
            result = backend.get_blob("//depot/main/foo.py", "12345")
        assert "x = 1" in result
        m.assert_called_once_with("p4", "/ws", "//depot/main/foo.py", "12345")

    def test_list_changed_files_marshals_dto(self) -> None:
        backend = self._backend()
        rows = [
            {"old_path": "//depot/a.py", "new_path": "//depot/a.py",
             "change_type": "modified", "is_binary": False},
        ]
        with patch("intentdiff.rust_core.vcs_backend_changed_files", return_value=rows):
            result = backend.list_changed_files("1", "2")
        assert result[0].change_type == "modified"

    def test_list_working_tree_changes_marshals_dto(self) -> None:
        backend = self._backend()
        rows = [
            {"old_path": None, "new_path": "//depot/new.py",
             "change_type": "added", "is_binary": False},
        ]
        with patch(
            "intentdiff.rust_core.vcs_backend_working_tree_changes", return_value=rows
        ) as m:
            result = backend.list_working_tree_changes()
        assert result[0].change_type == "added"
        m.assert_called_once_with("p4", "/ws", "default")


# ===========================================================================
# Ref / revision input validation (F6-4)
# ===========================================================================


class TestRefValidation:
    """Revision specifiers must be validated before being passed to VCS clients."""

    # ── Mercurial (validated in the core; see cargo `hg_ref_validation`) ─────

    @staticmethod
    def _hg_backend() -> HgVcsBackend:
        with patch("intentdiff.rust_core.vcs_backend_resolve_root", return_value="/fake"):
            return HgVcsBackend("/fake")

    def test_hg_get_blob_rejects_revset_expression(self) -> None:
        # A revset expression must be rejected by the core before any hg invocation.
        with pytest.raises(ValueError, match="Unsafe Mercurial revision"):
            self._hg_backend().get_blob("file.py", "tip or branch(default)")

    def test_hg_list_changed_files_rejects_injection(self) -> None:
        with pytest.raises(ValueError, match="Unsafe Mercurial revision"):
            self._hg_backend().list_changed_files("abc123", "tip; rm -rf /")

    # ── SVN (validated in the core; see cargo `svn_ref_validation`) ─────────

    @staticmethod
    def _svn_backend() -> SvnVcsBackend:
        backend = SvnVcsBackend.__new__(SvnVcsBackend)
        backend._root = "/fake"
        backend._repo_url = None
        return backend

    def test_svn_get_blob_rejects_shell_injection(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SVN revision"):
            self._svn_backend().get_blob("file.py", "HEAD; rm -rf /")

    def test_svn_list_changed_files_rejects_pipe_in_ref_a(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SVN revision"):
            self._svn_backend().list_changed_files("1|echo evil", "2")

    # ── Perforce (validated in the core; see cargo `p4_ref_and_depot_validation`) ─

    @staticmethod
    def _p4_backend() -> PerforceVcsBackend:
        with patch("intentdiff.rust_core.vcs_backend_resolve_root", return_value="/ws"):
            return PerforceVcsBackend("/ws")

    def test_p4_ref_rejects_at_injection(self) -> None:
        # A changelist with an embedded @ would smuggle a second revision specifier.
        with pytest.raises(ValueError, match="Unsafe Perforce"):
            self._p4_backend().get_blob("//depot/file.py", "12345@admin")

    def test_p4_depot_path_rejects_embedded_at(self) -> None:
        with pytest.raises(ValueError, match="'@' or '#'"):
            self._p4_backend().get_blob("//depot/file@label", "12345")

    def test_p4_depot_path_rejects_embedded_hash(self) -> None:
        with pytest.raises(ValueError, match="'@' or '#'"):
            self._p4_backend().get_blob("//depot/file#123", "12345")
