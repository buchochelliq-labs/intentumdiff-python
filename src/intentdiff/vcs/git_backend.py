"""
intentdiff.vcs.git_backend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Git VCS backend — a thin wrapper over the shared Rust VCS module (#98, A2.4.4).

The git operations (root discovery, blob read, changed-file listing, merge-base) live
in the Rust core (``crates/rust-core-host/src/vcs_backend.rs``); this class only marshals
to/from the :class:`ChangedFile` DTO. The working-tree file read stays here — it is plain
disk I/O, not VCS logic.

Security
--------
All file paths are validated against the repository root to prevent path-traversal
attacks before any I/O is performed.
"""

from __future__ import annotations

import os
from pathlib import Path

from intentdiff.vcs._safe_path import safe_relative_path, safe_working_tree_path
from intentdiff.vcs.base import ChangedFile, VcsBackend


def _safe_path(file_path: str) -> str:
    """Validate *file_path* is relative and contains no ``..`` components."""
    return safe_relative_path(file_path)


class GitVcsBackend(VcsBackend):
    """Git VCS backend — thin wrapper over the Rust ``vcs_backend`` (git dialect).

    Parameters
    ----------
    repo_path:
        Path to any directory inside the git repository; the work-tree root is
        resolved by the core (raises ``ValueError`` for a non-repo path).
    """

    _VCS = "git"

    def __init__(self, repo_path: str | os.PathLike[str] = ".") -> None:
        from intentdiff import rust_core

        self._root = rust_core.vcs_backend_resolve_root(self._VCS, os.fspath(repo_path))

    # ------------------------------------------------------------------
    # VcsBackend implementation
    # ------------------------------------------------------------------

    def get_blob(self, path: str, ref: str) -> str:
        """Return the content of *path* at git ref *ref* ("" when absent)."""
        from intentdiff import rust_core

        return rust_core.vcs_backend_get_blob(self._VCS, self._root, _safe_path(path), ref)

    def list_changed_files(self, ref_a: str, ref_b: str) -> list[ChangedFile]:
        """Return files that changed between git refs *ref_a* and *ref_b*."""
        from intentdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_changed_files(
                self._VCS, self._root, ref_a, ref_b
            )
        ]

    def get_working_file(self, path: str) -> str:
        """Return the on-disk content of *path* in the working tree."""
        full_path = safe_working_tree_path(self._root, _safe_path(path))
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def list_working_tree_changes(self, ref: str = "HEAD") -> list[ChangedFile]:
        """Return uncommitted working-tree changes vs *ref* (binaries skipped)."""
        from intentdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_working_tree_changes(
                self._VCS, self._root, ref
            )
        ]

    def resolve_root(self) -> Path:
        """Return the git repository root (the directory containing ``.git``)."""
        return Path(self._root)

    def get_merge_base(self, ref_a: str, ref_b: str) -> str:
        """Return the hexsha of the common ancestor of *ref_a* and *ref_b*."""
        from intentdiff import rust_core

        return rust_core.vcs_backend_merge_base(self._VCS, self._root, ref_a, ref_b)
