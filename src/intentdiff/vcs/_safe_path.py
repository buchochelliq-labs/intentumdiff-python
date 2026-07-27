"""
intentdiff.vcs._safe_path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared helpers for validating VCS file paths before passing them to backend
commands or constructing filesystem paths.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def safe_relative_path(file_path: str) -> str:
    """Validate that *file_path* is relative and contains no traversal."""
    if "\\" in file_path:
        raise ValueError(
            f"Unsafe file path: {file_path!r} - must use forward slashes."
        )

    windows = PureWindowsPath(file_path)
    if windows.is_absolute() or windows.drive:
        raise ValueError(
            f"Unsafe file path: {file_path!r} - must be relative, not absolute."
        )

    safe = PurePosixPath(file_path)
    if safe.is_absolute():
        raise ValueError(
            f"Unsafe file path: {file_path!r} - must be relative, not absolute."
        )
    if ".." in safe.parts:
        raise ValueError(
            f"Unsafe file path: {file_path!r} - must not contain '..' traversal."
        )
    return str(safe)


def safe_working_tree_path(root: str | Path, file_path: str) -> Path:
    """Return a repository-contained on-disk path for *file_path* under *root*."""
    safe = safe_relative_path(file_path)
    root_path = Path(root).resolve()
    candidate = (root_path / safe).resolve(strict=False)
    try:
        candidate.relative_to(root_path)
    except ValueError:
        raise ValueError(
            f"Unsafe file path: {file_path!r} - resolved path escapes {root_path!s}."
        ) from None
    return candidate
