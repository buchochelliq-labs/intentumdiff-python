"""
intentumdiff.vcs.perforce_backend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Perforce (Helix Core) VCS backend — a thin wrapper over the shared Rust VCS module
(#98, A2.4.4).

The Perforce operations (client-root discovery, blob read, changed-file listing, opened-file
status) live in the Rust core (``crates/rust-core-host/src/vcs_backend.rs``), which shells the
``p4`` CLI with tagged (``-ztag``) output; this class only marshals to/from the
:class:`ChangedFile` DTO. p4python is no longer used — the core is the single implementation
shared by every binding.

.. note:: **Breaking change (A2.4.4).** This backend previously took an injected, pre-connected
   ``P4`` object (p4python). It now shells the ``p4`` CLI, which resolves its connection the
   standard Perforce way — from the environment (``P4PORT`` / ``P4USER`` / ``P4CLIENT``) or a
   ``P4CONFIG`` file discovered from the working directory — matching how the git/hg/svn
   backends resolve their connection. The constructor no longer accepts a ``P4`` object.

Ref syntax
----------
``ref`` strings map to Perforce changelist numbers (as strings, e.g. ``"12345"``) or label
specs (e.g. ``"rel-1.0"``); depot paths (``//depot/...``) are validated in the core, which
rejects embedded ``@``/``#`` revision specifiers (an #88 filespec-injection control).
"""

from __future__ import annotations

import os
from pathlib import Path

from intentumdiff.vcs._safe_path import safe_working_tree_path
from intentumdiff.vcs.base import ChangedFile, VcsBackend


class PerforceVcsBackend(VcsBackend):
    """Perforce VCS backend — thin wrapper over the Rust ``vcs_backend`` (p4 dialect).

    Parameters
    ----------
    repo_path:
        Path to any directory inside the Perforce client workspace; the client root is
        resolved by the core (``p4 info``) unless *client_root* is given.  The ``p4`` CLI
        resolves the server connection from the environment / ``P4CONFIG``.
    client_root:
        The local workspace (client) root path.  When ``None``, resolved from ``p4 info``.
    """

    _VCS = "p4"

    def __init__(
        self,
        repo_path: str | os.PathLike[str] = ".",
        client_root: str | os.PathLike[str] | None = None,
    ) -> None:
        from intentumdiff import rust_core

        if client_root is not None:
            self._root = os.fspath(client_root)
        else:
            self._root = rust_core.vcs_backend_resolve_root(self._VCS, os.fspath(repo_path))

    # ------------------------------------------------------------------
    # VcsBackend implementation
    # ------------------------------------------------------------------

    def get_blob(self, path: str, ref: str) -> str:
        """Return file content at Perforce changelist/label *ref* ("" when absent).

        *path* is a depot path (e.g. ``//depot/main/src/foo.py``); the core joins it with the
        ``@ref`` revision specifier and runs ``p4 print``.
        """
        from intentumdiff import rust_core

        return rust_core.vcs_backend_get_blob(self._VCS, self._root, path, ref)

    def list_changed_files(self, ref_a: str, ref_b: str) -> list[ChangedFile]:
        """Return files changed between Perforce changelists *ref_a* and *ref_b*."""
        from intentumdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_changed_files(
                self._VCS, self._root, ref_a, ref_b
            )
        ]

    def get_working_file(self, path: str) -> str:
        """Return on-disk content of *path* (relative to the workspace root)."""
        full_path = safe_working_tree_path(self._root, path)
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def list_working_tree_changes(self, ref: str = "default") -> list[ChangedFile]:
        """Return files opened in the default pending changelist (``p4 opened``)."""
        from intentumdiff import rust_core

        return [
            ChangedFile(**row)
            for row in rust_core.vcs_backend_working_tree_changes(
                self._VCS, self._root, ref
            )
        ]

    def resolve_root(self) -> Path:
        """Return the Perforce workspace (client) root path."""
        return Path(self._root)
