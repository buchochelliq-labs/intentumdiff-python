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


