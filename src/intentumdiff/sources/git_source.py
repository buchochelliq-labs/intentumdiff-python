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
import subprocess
from collections.abc import Iterator
from pathlib import Path

from intentumdiff import content_type as _content_type
from intentumdiff.sources.base import Source
from intentumdiff.vcs._safe_path import safe_policy_path, safe_relative_path, safe_working_tree_path
from intentumdiff.vcs.git_cli import (
    parse_name_status_z as _parse_name_status_z,
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


# Change-type codes that produce binary-safe text blobs on both sides.
_TEXT_CHANGE_TYPES = frozenset({"A", "D", "M", "R", "C"})


# Sentinel new_ref values recognised by iter_changed_sources.
_REF_STAGED = ":staged"     # compare HEAD to the git index (staged files only)
_REF_UNPUSHED = ":unpushed" # compare remote tracking branch to HEAD (unpushed commits)


_ChangedSource = tuple[str, str, str, str, str | None]


def _is_python_path(path: str) -> bool:
    return path.lower().endswith((".py", ".pyi"))


def _decode_git_path(path: bytes) -> str:
    # A Git pathname need not be UTF-8 on every filesystem. Without a
    # lossless byte transport to Rust, reject rather than invent a new name.
    try:
        return path.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnicodeError("Invalid UTF-8 in Git pathname") from exc


def _decode_nul_paths(data: bytes, *, source: str) -> list[str]:
    """Require a complete, nonempty Git -z path inventory (#239)."""
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise RuntimeError(f"Incomplete {source} output: missing NUL terminator")
    raw_paths = data[:-1].split(b"\0")
    if any(not path for path in raw_paths):
        raise RuntimeError(f"Empty path in {source} output")
    return [_decode_git_path(path) for path in raw_paths]


def _untracked_paths(repo_path: str) -> list[str]:
    data = run_git_bytes(repo_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    return sorted(_decode_nul_paths(data, source="git ls-files -z"))


def _read_working_tree_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        # A failed read of an enumerated untracked file is not evidence that
        # the file is binary; skipping it would certify an incomplete diff (#242).
        raise RuntimeError(
            f"Failed to read untracked working-tree source {path}: {exc}"
        ) from exc
    # Content-based routing: binary/image assets are skipped (reviewed via the
    # perceptual asset diff), never decoded and sent to the text engine.
    return _decode_text_or_none(data)


def _read_commit_paths_batch(
    repo_path: str,
    rev: str,
    paths: list[str],
) -> dict[str, str]:
    unique_paths = list(dict.fromkeys(paths))
    if not unique_paths:
        return {}
    if any("\n" in path or "\r" in path for path in unique_paths):
        raise ValueError("git cat-file batch path contains a newline")

    request = "".join(f"{rev}:{path}\n" for path in unique_paths).encode("utf-8")
    output = run_git_bytes(repo_path, ["cat-file", "--batch"], input_bytes=request)
    result: dict[str, str] = {}
    pos = 0
    for path in unique_paths:
        header_end = output.find(b"\n", pos)
        if header_end < 0:
            raise RuntimeError("malformed git cat-file batch output")
        header = output[pos:header_end]
        pos = header_end + 1
        if header.endswith(b" missing"):
            # Every requested non-addition path must exist in the base tree.
            # An empty fallback would manufacture semantic change evidence (#241).
            raise RuntimeError(f"Missing tracked Git baseline {rev}:{path}")
        parts = header.split()
        if len(parts) != 3 or parts[1] != b"blob":
            raise RuntimeError("malformed git cat-file batch blob header")
        try:
            size = int(parts[2])
        except ValueError as exc:
            raise RuntimeError("malformed git cat-file batch size") from exc
        if size < 0 or pos + size >= len(output) or output[pos + size] != 0x0A:
            raise RuntimeError("incomplete git cat-file batch blob payload")
        content = output[pos : pos + size]
        pos += size + 1  # consume mandatory delimiter
        try:
            result[path] = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise UnicodeError(
                f"Invalid UTF-8 in tracked Git baseline {rev}:{path}"
            ) from exc
    if pos != len(output):
        raise RuntimeError("Unexpected extra git cat-file batch output")
    return result


def collect_working_tree_python_sources_fast(
    repo_path: "str | os.PathLike[str]",
    old_ref: str,
) -> "tuple[list[_ChangedSource], str | None] | None":
    """
    Fast working-tree collector for the certified Rust Python batch path.

    Returns ``None`` when the conservative Git CLI path cannot be used and the
    caller should fall back to ``iter_changed_sources``.  A non-empty fallback
    reason means the diff contains a non-Python changed file, so the certified
    Python-only path must not run.
    """
    try:
        working_dir = resolve_repo_root(repo_path)
        # git resolves the ref on each call; using it directly (vs a pre-resolved
        # sha) is equivalent here and avoids an extra round-trip.
        old_rev = old_ref
        diff_output = run_git_bytes(working_dir, ["diff", "--name-status", "-z", old_rev])
        entries = _parse_name_status_z(diff_output)
        if any(code not in _TEXT_CHANGE_TYPES for code, _, _ in entries):
            return ([], "Unsupported Git change status requires Rust review")
        untracked_paths = _untracked_paths(working_dir)
        if not entries and not untracked_paths:
            return [], None
        for _code, old_path, new_path in entries:
            if not (_is_python_path(old_path) or _is_python_path(new_path)):
                return (
                    [],
                    "certified commit JSON requires all changed files to be Python",
                )
        for untracked_path in untracked_paths:
            if not _is_python_path(untracked_path):
                return (
                    [],
                    "certified commit JSON requires all changed files to be Python",
                )

        staged_output = run_git_bytes(
            working_dir,
            ["diff", "--cached", "--name-only", "-z", old_rev],
        )
        staged_paths = set(
            _decode_nul_paths(staged_output, source="git diff --cached --name-only -z")
        )
        old_paths = [old_path for code, old_path, _new_path in entries if code != "A"]
        old_contents = _read_commit_paths_batch(working_dir, old_rev, old_paths)
        sources: list[_ChangedSource] = []
        root = Path(working_dir)
        for code, old_path, new_path in entries:
            _safe_file_path(old_path)
            _safe_file_path(new_path)
            old_content = "" if code == "A" else old_contents[old_path]
            if code == "D":
                new_content = ""
            else:
                disk_path = safe_working_tree_path(root, new_path)
                if not disk_path.exists():
                    new_content = ""
                else:
                    new_content = disk_path.read_text(encoding="utf-8")
            staging_status = (
                "staged"
                if old_path in staged_paths or new_path in staged_paths
                else "unstaged"
            )
            sources.append((old_content, new_content, old_path, new_path, staging_status))
        for untracked_path in untracked_paths:
            _safe_file_path(untracked_path)
            disk_path = safe_working_tree_path(root, untracked_path)
            new_content = _read_working_tree_text(disk_path)
            if new_content is None:
                continue
            sources.append(("", new_content, untracked_path, untracked_path, "untracked"))
        return sources, None
    except UnicodeError:
        # The optimizer must not turn a bad source encoding into an altered,
        # successful semantic review or silently reroute it as a normal decline.
        raise
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


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
