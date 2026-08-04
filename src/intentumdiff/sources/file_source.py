"""
intentumdiff.sources.file_source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Compare two files on the local filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path

from intentumdiff.sources.base import Source


class FileSource(Source):
    """
    Compare two local files.

    Parameters
    ----------
    old_path:
        Path to the old (before) version of the file.
    new_path:
        Path to the new (after) version of the file.
    language_hint:
        Optional language override.
    filename:
        Display filename used in diff output.  Defaults to the basename of
        ``new_path``.
    """

    def __init__(
        self,
        old_path: str | os.PathLike[str],
        new_path: str | os.PathLike[str],
        language_hint: str | None = None,
        filename: str | None = None,
    ) -> None:
        self._old_path = Path(old_path)
        self._new_path = Path(new_path)
        self._language_hint = language_hint
        self._filename = filename or self._new_path.name

    def get_content(self) -> tuple[str, str, str, str | None]:
        old_content = self._old_path.read_text(encoding="utf-8", errors="replace")
        new_content = self._new_path.read_text(encoding="utf-8", errors="replace")
        return old_content, new_content, self._filename, self._language_hint
