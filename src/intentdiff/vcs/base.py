"""
intentdiff.vcs.base
~~~~~~~~~~~~~~~~~~~~~~~~~~

Abstract base class for VCS backends and the ``ChangedFile`` data model.

All VCS backends must implement the five abstract diff methods.  Two
non-abstract merge hooks (``get_merge_base``, ``get_conflict_content``) are
provided as stubs for future semantic-merge support.

``ref`` is always a plain ``str`` at every boundary — each backend interprets
its own ref syntax (git SHA / SVN revision number / Perforce changelist /
Mercurial changeset hash).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ChangedFile:
    """Describes a single file-level change between two VCS revisions.

    Attributes
    ----------
    old_path:
        Path to the file in the *old* revision, or ``None`` for newly-added
        files.
    new_path:
        Path to the file in the *new* revision, or ``None`` for deleted files.
    change_type:
        One of ``"added"``, ``"deleted"``, ``"modified"``, ``"renamed"``, or
        ``"copied"``.
    is_binary:
        ``True`` when the backend detected the file as binary (not diffable as
        text).
    """

    old_path: str | None
    new_path: str | None
    change_type: Literal["added", "deleted", "modified", "renamed", "copied"]
    is_binary: bool = field(default=False)


class VcsBackend(ABC):
    """Minimal abstract VCS interface for IntentDiff.

    All backends must implement the five abstract diff methods:

    * :meth:`get_blob` — file content at a revision
    * :meth:`list_changed_files` — changed-file list between two refs
    * :meth:`get_working_file` — on-disk content (working tree)
    * :meth:`list_working_tree_changes` — uncommitted changes
    * :meth:`resolve_root` — repository root path

    Two non-abstract merge hooks are provided as ``NotImplementedError`` stubs
    for future semantic-merge support:

    * :meth:`get_merge_base`
    * :meth:`get_conflict_content`
    """

    # ------------------------------------------------------------------
    # Required: diff interface
    # ------------------------------------------------------------------

    @abstractmethod
    def get_blob(self, path: str, ref: str) -> str:
        """Return the content of *path* at revision *ref* as a UTF-8 string.

        Returns an empty string when *path* does not exist at *ref* (e.g. for
        newly-added files queried against the old ref).

        Parameters
        ----------
        path:
            File path relative to the repository root, using forward slashes.
        ref:
            VCS-specific revision identifier (SHA, revision number, etc.).
        """

    @abstractmethod
    def list_changed_files(self, ref_a: str, ref_b: str) -> list[ChangedFile]:
        """Return the list of files that changed between *ref_a* and *ref_b*.

        Added files have ``old_path=None``.
        Deleted files have ``new_path=None``.
        Renamed files have both paths set and ``change_type="renamed"``.
        """

    @abstractmethod
    def get_working_file(self, path: str) -> str:
        """Return the current on-disk content of *path* as a UTF-8 string.

        Returns an empty string when the file does not exist on disk.
        """

    @abstractmethod
    def list_working_tree_changes(self, ref: str = "HEAD") -> list[ChangedFile]:
        """Return uncommitted changes in the working tree relative to *ref*.

        The default *ref* value (``"HEAD"``) is a git convention; backends
        for other VCS systems should document their equivalent default.
        """

    @abstractmethod
    def resolve_root(self) -> Path:
        """Return the absolute path to the repository root."""

    # ------------------------------------------------------------------
    # Future: semantic-merge hooks (non-abstract stubs)
    # ------------------------------------------------------------------

    def get_merge_base(self, ref_a: str, ref_b: str) -> str:  # pragma: no cover
        """Return the common ancestor revision of *ref_a* and *ref_b*.

        Raises ``NotImplementedError`` by default.  Implement in backends that
        support semantic merge.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not yet implement get_merge_base()"
        )

    def get_conflict_content(self, path: str) -> tuple[str, str, str]:  # pragma: no cover
        """Return ``(base, ours, theirs)`` content for a conflicted *path*.

        Raises ``NotImplementedError`` by default.  Implement in backends that
        support semantic merge.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not yet implement get_conflict_content()"
        )
