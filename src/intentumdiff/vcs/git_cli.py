"""
intentumdiff.vcs.git_cli
~~~~~~~~~~~~~~~~~~~~~~~~

Thin git-CLI helpers for the Python shell — repo discovery + a subprocess runner —
that replace the GitPython object model (#98, A2.4.3). Repo discovery delegates to the
shared Rust core (``rust_core.git_repo_toplevel``, which shells ``git rev-parse
--show-toplevel``); the raw ``run_git_bytes`` runner covers the few plumbing calls
(``ls-tree``/``cat-file``) that do not yet have a dedicated core entrypoint.

``NotAGitRepositoryError`` is the native replacement for GitPython's
``InvalidGitRepositoryError`` / ``NoSuchPathError``. It subclasses ``ValueError`` so
existing ``except ValueError`` handlers keep working during the migration.
"""

from __future__ import annotations

import os
import shutil
import subprocess


class NotAGitRepositoryError(ValueError):
    """Raised when a path is not inside a usable git work tree.

    Replaces GitPython's ``InvalidGitRepositoryError`` and ``NoSuchPathError`` (a path
    that does not exist or is not a repo). Subclasses ``ValueError`` for back-compat
    with handlers written against the GitPython era.
    """


def resolve_repo_root(path: "str | os.PathLike[str]") -> str:
    """Return the absolute work-tree root for the repository containing *path*.

    Delegates to the Rust core (``git rev-parse --show-toplevel``). Accepts any
    directory inside the repo (GitPython's ``search_parent_directories=True``
    equivalent). Raises :class:`NotAGitRepositoryError` when *path* is not inside a
    git work tree (non-repo, missing path, or a bare repository).
    """
    from intentumdiff import rust_core

    try:
        return rust_core.git_repo_toplevel(os.fspath(path))
    except ValueError as exc:  # rust_core raises ValueError on non-repo / bare / missing
        raise NotAGitRepositoryError(
            f"{os.fspath(path)!r} is not inside a git repository work tree."
        ) from exc


def run_git_bytes(
    repo_path: "str | os.PathLike[str]",
    args: list[str],
    *,
    input_bytes: bytes | None = None,
) -> bytes:
    """Run ``git -C <repo_path> <args>`` and return stdout bytes (raises on failure).

    A fixed ``git`` executable, no shell — the plumbing runner for the few tree/blob
    reads without a dedicated core entrypoint yet.
    """
    git_exe = shutil.which("git")
    if git_exe is None:
        raise NotAGitRepositoryError("git executable not found on PATH")
    proc = subprocess.run(  # noqa: S603 - fixed git executable, arg list, no shell.
        [git_exe, "-C", os.fspath(repo_path), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout


def parse_name_status_z(data: bytes) -> "list[tuple[str, str, str]]":
    """Decode a complete Git name-status -z stream without losing file identities.

    Git uses NUL terminators to delimit status tokens and raw pathname bytes.
    A malformed stream or an undecodable path is an integrity failure, never a
    successful partial change inventory (Python #240).
    """
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise RuntimeError("Incomplete git name-status -z: missing NUL terminator")

    tokens = data[:-1].split(b"\0")
    entries: list[tuple[str, str, str]] = []
    idx = 0
    while idx < len(tokens):
        try:
            status = tokens[idx].decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise UnicodeError("Invalid ASCII Git change status") from exc
        idx += 1
        if (
            not status
            or status[0] not in "ACDMRTUXB"
            or (len(status) > 1 and (status[0] not in "RC" or not status[1:].isdigit()))
        ):
            raise RuntimeError(f"Invalid git name-status code: {status!r}")

        code = status[0]
        needed = 2 if code in {"R", "C"} else 1
        if len(tokens) - idx < needed:
            raise RuntimeError(
                f"Truncated git name-status -z: {code!r} requires {needed} path(s)"
            )

        raw_paths = tokens[idx : idx + needed]
        idx += needed
        if any(not path for path in raw_paths):
            raise RuntimeError("Empty filename in git name-status -z")
        try:
            paths = [path.decode("utf-8", errors="strict") for path in raw_paths]
        except UnicodeDecodeError as exc:
            raise UnicodeError("Invalid UTF-8 in Git change filename") from exc
        old_path = paths[0]
        new_path = paths[1] if needed == 2 else old_path
        entries.append((code, old_path, new_path))
    return entries
