"""
intentumdiff.sources.live_buffer_source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Source that compares a git-committed version of a file against an in-memory
buffer — the primary input for ``LiveServer`` (keystroke-level diffing).

Security
────────
File paths are validated against the repository root to prevent path-traversal
attacks before any git I/O is performed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from intentumdiff.sources.base import Source
from intentumdiff.vcs._safe_path import safe_relative_path
from intentumdiff.vcs.git_cli import resolve_repo_root, run_git_bytes

if TYPE_CHECKING:
    from intentumdiff.core.models import EditDelta


class LiveBufferSource(Source):
    """Diff the committed (``ref``) version of a file against a live in-memory buffer.

    Intended for editor integrations and ``LiveServer``: the editor sends the
    current buffer contents and (optionally) incremental edit deltas so the
    differ can skip a full re-parse.

    Parameters
    ----------
    repo_path:
        Path to any directory inside the git repository (or the root itself).
    file_path:
        Repository-relative path to the file, e.g. ``"src/main.py"``.
        Must not be absolute or contain ``".."``.
    live_content:
        The current (unsaved / in-buffer) content to compare against.
    ref:
        Git ref to use as the ``old`` version.  Defaults to ``"HEAD"``.
    language_hint:
        Optional language override (e.g. ``"python"``).  Pass ``None`` to
        auto-detect from the file extension.
    edit_deltas:
        Optional list of ``EditDelta`` objects describing the incremental
        byte-range edits that produced ``live_content`` from the previously
        known version.  When provided, ``SemanticDiffer`` can reuse an
        existing tree-sitter parse tree instead of re-parsing from scratch.
    """

    def __init__(
        self,
        repo_path: str | Path,
        file_path: str,
        live_content: str,
        *,
        ref: str = "HEAD",
        language_hint: str | None = None,
        edit_deltas: "list[EditDelta] | None" = None,
    ) -> None:
        # Validate the file path before storing it.
        safe = PurePosixPath(file_path)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(
                f"Unsafe file path: {file_path!r} — must be relative and must not "
                "contain '..'."
            )

        self._repo_path = Path(repo_path)
        self._file_path = safe_relative_path(file_path)
        self._live_content = live_content
        self._ref = ref
        self._language_hint = language_hint
        self.edit_deltas: list[EditDelta] | None = edit_deltas

    # ------------------------------------------------------------------
    # Source interface
    # ------------------------------------------------------------------

    def get_content(self) -> tuple[str, str, str, str | None]:
        """Return ``(old_content, new_content, filename, language_hint)``.

        *old_content* is read from the committed blob at ``self._ref``.
        *new_content* is the in-memory buffer ``live_content``.
        """
        old_content = self._read_committed()
        filename = Path(self._file_path).name
        return old_content, self._live_content, filename, self._language_hint

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_committed(self) -> str:
        """Read the committed blob at ``self._ref:self._file_path``.

        Uses the git CLI (``cat-file blob``) rather than the GitPython object model
        (#98, A2.4.3); a file not tracked at this ref yields an empty old side.
        """
        root = resolve_repo_root(self._repo_path)
        try:
            data = run_git_bytes(
                root, ["cat-file", "blob", f"{self._ref}:{self._file_path}"]
            )
        except subprocess.CalledProcessError:
            # File not yet tracked at this ref — treat old side as empty.
            return ""
        return data.decode("utf-8", errors="replace")
