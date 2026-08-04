"""
intentumdiff.vcs.hg_backend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Mercurial VCS backend — a thin wrapper over the shared Rust VCS module (#98, A2.4.4).

The Mercurial operations (root discovery, blob read, changed-file listing, status parsing)
live in the Rust core (``crates/rust-core-host/src/vcs_backend.rs``), which shells the ``hg``
CLI directly; this class only marshals to/from the :class:`ChangedFile` DTO. python-hglib is
no longer used — the core is the single implementation shared by every binding. The
working-tree file read stays here (plain disk I/O, not VCS logic).

Ref syntax
----------
``ref`` strings map to Mercurial revision syntax: changeset hash prefix, revision number
(as a string), tag name, or bookmark name.  The special value ``"."`` refers to the current
working-directory parent.  Revision inputs are validated in the core (an #88 control).
"""

from __future__ import annotations

import os
from pathlib import Path

from intentumdiff.vcs._safe_path import safe_relative_path, safe_working_tree_path
from intentumdiff.vcs.base import ChangedFile, VcsBackend


class HgVcsBackend(VcsBackend):
    """Mercurial VCS backend — thin wrapper over the Rust ``vcs_backend`` (hg dialect).

    Parameters
    ----------
    repo_path:
        Path to any directory inside the Mercurial repository; the repository root is
        resolved by the core (``hg root``), which raises ``ValueError`` for a non-repo path.
    """

    _VCS = "hg"

    def __init__(self, repo_path: str | os.PathLike[str] = ".") -> None:
        from intentumdiff import rust_core

        self._root = rust_core.vcs_backend_resolve_root(self._VCS, os.fspath(repo_path))

    # ------------------------------------------------------------------
    # VcsBackend implementation
    # ------------------------------------------------------------------

    def get_blob(self, path: str, ref: str) -> str:
        """Return file content at Mercurial revision *ref* ("" when absent)."""
        from intentumdiff import rust_core

        return rust_core.vcs_backend_get_blob(
            self._VCS, self._root, safe_relative_path(path), ref
        )

    def list_changed_files(self, ref_a: str, ref_b: str) -> list[ChangedFile]:
        """Return files changed between Mercurial revisions *ref_a* and *ref_b*."""
        from intentumdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_changed_files(
                self._VCS, self._root, ref_a, ref_b
            )
        ]

    def get_working_file(self, path: str) -> str:
        """Return on-disk content of *path* in the Mercurial working directory."""
        full_path = safe_working_tree_path(self._root, path)
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def list_working_tree_changes(self, ref: str = ".") -> list[ChangedFile]:
        """Return uncommitted working-directory changes (``hg status``).

        The default *ref* value ``"."`` is the Mercurial working-directory parent revision
        (equivalent to git ``HEAD``); ``hg status`` always reports against it.
        """
        from intentumdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_working_tree_changes(
                self._VCS, self._root, ref
            )
        ]

    def resolve_root(self) -> Path:
        """Return the Mercurial repository root path."""
        return Path(self._root)
