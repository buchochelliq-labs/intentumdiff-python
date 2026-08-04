"""
intentumdiff.vcs.svn_backend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Subversion VCS backend — a thin wrapper over the shared Rust VCS module (#98, A2.4.4).

The Subversion operations (root discovery, blob read, changed-file listing, ``svn --xml``
parsing) live in the Rust core (``crates/rust-core-host/src/vcs_backend.rs``), which shells the
``svn`` CLI directly; this class only marshals to/from the :class:`ChangedFile` DTO. The XML
parsing that used to run here (defusedxml) is gone — the core is the single implementation
shared by every binding. The working-tree file read stays here (plain disk I/O, not VCS logic).

Ref syntax
----------
``ref`` strings map directly to SVN revision syntax: ``"HEAD"``, ``"BASE"``, ``"PREV"``, or an
integer revision number as a string (e.g. ``"42"``).  Revision inputs are validated in the core
(an #88 control).
"""

from __future__ import annotations

import os
from pathlib import Path

from intentumdiff.vcs._safe_path import safe_relative_path, safe_working_tree_path
from intentumdiff.vcs.base import ChangedFile, VcsBackend


class SvnVcsBackend(VcsBackend):
    """Subversion VCS backend — thin wrapper over the Rust ``vcs_backend`` (svn dialect).

    Parameters
    ----------
    working_copy:
        Path to the SVN working copy root.
    repo_url:
        Repository URL (e.g. ``svn+ssh://svn.example.com/repos/proj``).  When provided,
        :meth:`get_blob` and :meth:`list_changed_files` target the repository URL directly (via
        the core) and do not require the working copy to have the file checked out at that
        revision.
    """

    _VCS = "svn"

    def __init__(
        self,
        working_copy: str | os.PathLike[str],
        repo_url: str | None = None,
    ) -> None:
        # The caller supplies the working-copy root; the core resolves the true wc-root on
        # demand (resolve_root) rather than eagerly, so a bare path is accepted here.
        self._root = os.fspath(working_copy)
        self._repo_url = repo_url

    # ------------------------------------------------------------------
    # VcsBackend implementation
    # ------------------------------------------------------------------

    def get_blob(self, path: str, ref: str) -> str:
        """Return file content at SVN revision *ref* ("" when absent)."""
        from intentumdiff import rust_core

        return rust_core.vcs_backend_get_blob(
            self._VCS, self._root, safe_relative_path(path), ref, self._repo_url
        )

    def list_changed_files(self, ref_a: str, ref_b: str) -> list[ChangedFile]:
        """Return files changed between SVN revisions *ref_a* and *ref_b*."""
        from intentumdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_changed_files(
                self._VCS, self._root, ref_a, ref_b, self._repo_url
            )
        ]

    def get_working_file(self, path: str) -> str:
        """Return on-disk content of *path* in the SVN working copy."""
        full_path = safe_working_tree_path(self._root, path)
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def list_working_tree_changes(self, ref: str = "BASE") -> list[ChangedFile]:
        """Return uncommitted local modifications (``svn status --xml``).

        The default *ref* value ``"BASE"`` is the SVN equivalent of ``HEAD``; ``svn status``
        always compares against ``BASE``, so *ref* is accepted only for interface compatibility.
        """
        from intentumdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_working_tree_changes(
                self._VCS, self._root, ref
            )
        ]

    def resolve_root(self) -> Path:
        """Return the SVN working-copy root path (``svn info --show-item wc-root``)."""
        from intentumdiff import rust_core

        return Path(rust_core.vcs_backend_resolve_root(self._VCS, self._root))
