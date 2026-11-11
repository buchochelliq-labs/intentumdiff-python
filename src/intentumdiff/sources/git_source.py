"""
intentumdiff.sources.git_source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retrieve file content from git commits using GitPython.

Security
────────
All file paths are validated against the repository root to prevent
path-traversal attacks before any I/O is performed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from intentumdiff import content_type as _content_type
from intentumdiff.sources.base import Source
from intentumdiff.vcs._safe_path import safe_policy_path, safe_relative_path, safe_working_tree_path
from intentumdiff.vcs.git_cli import (
    resolve_repo_root,
    run_git_bytes,
)


def _decode_text_or_none(data: bytes) -> str | None:
    """Decode *data* as text, or return ``None`` when it is a binary/image asset.

    Uses the Rust magic-byte content-type detector (not the filename) so binary
    assets are never fed to the text engine — a PNG parsed as text explodes the
    CST past the plugin output limit.
    """
    if not _content_type.is_text_bytes(data):
        return None
    return data.decode("utf-8", errors="strict")


def _safe_file_path(file_path: str) -> str:
    """
    Validate that ``file_path`` does not escape the repository root.

    Returns the normalised posix-style path relative to the repo root.
    Raises ``ValueError`` on traversal attempts.
    """
    return safe_relative_path(file_path)


# Sentinel new_ref values recognised by iter_changed_sources.
_REF_STAGED = ":staged"     # compare HEAD to the git index (staged files only)
_REF_UNPUSHED = ":unpushed" # compare remote tracking branch to HEAD (unpushed commits)


def iter_changed_sources(
    repo_path: "str | os.PathLike[str]",
    old_ref: str,
    new_ref: str = "",
) -> "Iterator[tuple[str, str, str, str, str | None]]":
    """
    Yield ``(old_content, new_content, old_path, new_path, staging_status)``
    for every text file that changed between *old_ref* and *new_ref*.

    Special *new_ref* sentinels:
    * ``""`` (default) — working tree: all saved changes, annotated as
      ``"staged"`` or ``"unstaged"`` per file.
    * ``":staged"``   — only files in the git index (staged changes).
    * ``":unpushed"`` — commits on the current branch not yet pushed to the
      remote tracking branch (*old_ref* is ignored in this mode).

    For regular commit-to-commit diffs, ``staging_status`` is ``None``.

    Added files have ``old_content == ""`` and ``old_path == new_path``.
    Deleted files have ``new_content == ""`` and ``new_path == old_path``.
    Binary blobs and change types that cannot be diffed semantically are
    silently skipped.
    """
    # All four modes (working-tree "", ":staged", ":unpushed", commit-to-commit) are
    # dispatched by the shared Rust core (#98, A2.4.2) — one entrypoint every binding
    # shares. resolve_repo_root validates the path (NotAGitRepositoryError) and yields
    # the work-tree root the core operates on.
    from intentumdiff import rust_core

    root = resolve_repo_root(repo_path)
    yield from rust_core.changed_sources(root, old_ref, new_ref)


class GitSource(Source):
    """
    Compare a file between two git refs (commits, tags, or branch names).

    Parameters
    ----------
    repo_path:
        Path to the git repository (any directory inside it works — GitPython
        will locate the root).
    file_path:
        Path to the file relative to the repository root, using forward
        slashes (e.g. ``"src/foo.py"``).
    old_ref:
        Old git ref (commit SHA, tag, branch).  Defaults to ``"HEAD~1"``.
    new_ref:
        New git ref.  Defaults to ``"HEAD"``.
    language_hint:
        Optional language override; if omitted the plugin registry detects it.
    """

    def __init__(
        self,
        repo_path: str | os.PathLike[str],
        file_path: str,
        old_ref: str = "HEAD~1",
        new_ref: str = "HEAD",
        language_hint: str | None = None,
    ) -> None:
        # resolve_repo_root validates the path + locates the work-tree root (raising
        # NotAGitRepositoryError for a non-repo path); the blob reads are Rust-
        # authoritative (#98, A2.4.1): resolve ref + cat-file in the core.
        self._repo_path = resolve_repo_root(repo_path)
        self._file_path = _safe_file_path(file_path)
        self._old_ref = old_ref
        self._new_ref = new_ref
        self._language_hint = language_hint

    def get_content(self) -> tuple[str, str, str, str | None]:
        from intentumdiff import rust_core

        old_content, new_content = rust_core.git_source_content(
            self._repo_path, self._file_path, self._old_ref, self._new_ref
        )
        return old_content, new_content, self._file_path, self._language_hint

    def guardrail_policy_paths(self) -> tuple[str, str]:
        """Use repository policy context without inspecting a historical blob's live target.

        Commit-to-commit Git data comes from object storage.  A checkout may
        meanwhile replace the tracked file with an external symlink; following
        that unrelated final component must not invalidate historical review.
        Resolve and validate only its parent (where policy discovery begins).
        """
        path = safe_policy_path(self._repo_path, self._file_path)
        return path, path

    @classmethod
    def working_tree(
        cls,
        repo_path: "str | os.PathLike[str]",
        file_path: str,
        *,
        ref: str = "HEAD",
        language_hint: str | None = None,
    ) -> "WorkingTreeSource":
        """
        Convenience factory — diff the committed version of a file at ``ref``
        (default: ``HEAD``) against the current un-staged working-tree copy.

        Returns a ``WorkingTreeSource`` instance.
        """
        return WorkingTreeSource(
            repo_path=repo_path,
            file_path=file_path,
            ref=ref,
            language_hint=language_hint,
        )


class StagedSource(GitSource):
    """Select one file from Rust's index comparison, preserving rename paths."""

    def __init__(self, repo_path: str | os.PathLike[str], file_path: str,
                 ref: str = "HEAD") -> None:
        super().__init__(repo_path, file_path, old_ref=ref, new_ref=_REF_STAGED)
        self._old_path = self._file_path
        self._new_path = self._file_path

    def get_content(self) -> tuple[str, str, str, str | None]:
        from intentumdiff import rust_core

        sources = list(iter_changed_sources(self._repo_path, self._old_ref, _REF_STAGED))
        # Prefer the destination when a path is both a rename source and target.
        selected = next((row for row in sources if row[3] == self._file_path), None)
        if selected is None:
            selected = next((row for row in sources if row[2] == self._file_path), None)
        if selected is not None:
            old, new, self._old_path, self._new_path, _status = selected
            return old, new, self._new_path, self._language_hint
        # The text inventory also omits binary entries. Read the actual index
        # bytes before treating an absent row as an unchanged text source.
        self._old_path = self._new_path = self._file_path
        index_bytes = run_git_bytes(
            self._repo_path, ["cat-file", "blob", f":{self._file_path}"]
        )
        new = _decode_text_or_none(index_bytes)
        if new is None:
            raise ValueError(f"Staged source {self._file_path!r} is not text")
        old, _ = rust_core.git_source_content(
            self._repo_path, self._file_path, self._old_ref, self._old_ref
        )
        return old, new, self._file_path, self._language_hint

    def display_names(self) -> tuple[str, str]:
        return self._old_path, self._new_path

    def guardrail_policy_paths(self) -> tuple[str, str]:
        return (safe_policy_path(self._repo_path, self._old_path),
                safe_policy_path(self._repo_path, self._new_path))


class WorkingTreeSource(Source):
    """
    Compare the committed version of a file against the current working tree.

    The **old** content is the blob stored at ``ref`` (default: ``HEAD``).
    The **new** content is read directly from the file on disk — i.e. whatever
    the developer has saved but not yet staged or committed.

    Parameters
    ----------
    repo_path:
        Path to the git repository (or any directory inside it).
    file_path:
        Path to the file relative to the repository root, using forward
        slashes (e.g. ``"src/foo.py"``).
    ref:
        Git ref to use as the "old" version.  Defaults to ``"HEAD"``.
    language_hint:
        Optional language override; if omitted the plugin registry detects it.
    """

    def __init__(
        self,
        repo_path: "str | os.PathLike[str]",
        file_path: str,
        ref: str = "HEAD",
        language_hint: str | None = None,
    ) -> None:
        # See GitSource.__init__: resolve_repo_root validates + locates the root; the
        # blob read (old side) + working-tree read (new side) are Rust (A2.4.1).
        self._repo_path = resolve_repo_root(repo_path)
        self._file_path = _safe_file_path(file_path)
        self._ref = ref
        self._language_hint = language_hint

    def get_content(self) -> tuple[str, str, str, str | None]:
        from intentumdiff import rust_core

        old_content, new_content = rust_core.working_tree_source_content(
            self._repo_path, self._file_path, self._ref
        )
        return old_content, new_content, self._file_path, self._language_hint

    def guardrail_policy_paths(self) -> tuple[str, str]:
        """Locate working-tree guardrails in the actual repository root."""
        path = str(safe_working_tree_path(self._repo_path, self._file_path))
        return path, path
