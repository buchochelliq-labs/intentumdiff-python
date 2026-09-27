"""
intentumdiff.core.diffignore
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``.diffignore`` file loading and path filtering.

A ``.diffignore`` file placed in the repository root follows the same
**gitwildmatch** syntax as ``.gitignore``:

* Blank lines and lines beginning with ``#`` are ignored.
* A leading ``/`` anchors the pattern to the root.
* A trailing ``/`` means "match this directory (and everything under it)".
* ``*`` matches any character sequence that does not contain ``/``.
* ``**`` matches across path components.
* A leading ``!`` negates a previous pattern.

Examples::

    # Ignore generated files
    target/
    dist/
    *.lock
    **/generated/**
    !important.lock        # keep this specific lock file
"""

from __future__ import annotations

import logging
from pathlib import Path
import json
from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: Conventional filename — place in the repo / directory root.
DIFFIGNORE_FILENAME = ".diffignore"


def load_diffignore(root: str | Path) -> "DiffIgnore | None":
    """
    Look for a ``.diffignore`` file in *root* and return a :class:`DiffIgnore`
    instance if found, or ``None`` when the file does not exist.

    A warning is logged (but not raised) if the file exists but cannot be read.
    """
    path = Path(root) / DIFFIGNORE_FILENAME
    if not path.is_file():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None

    return DiffIgnore(text)


class DiffIgnore:
    """Thin adapter over Rust ignore rules; construct with raw file contents."""

    __slots__ = ("_files",)

    def __init__(self, patterns: str = "", *, directory_rules: Mapping[str, str] | None = None) -> None:
        if not isinstance(patterns, str):
            raise TypeError("DiffIgnore requires raw ignore-file text")
        self._files = [{"directory": "", "content": patterns}]
        self._files.extend({"directory": directory, "content": content} for directory, content in (directory_rules or {}).items())
        self._matches([])  # validate rules through the engine eagerly

    def _matches(self, paths: list[dict]) -> list[bool]:
        from intentumdiff.rust_core import _required_engine_json
        result = _required_engine_json("match_ignore_rules", json.dumps({"files": self._files, "paths": paths}), result_type=list)
        if len(result) != len(paths) or any(type(value) is not bool for value in result):
            raise RuntimeError("Rust ignore result has invalid fields")
        return result

    def is_ignored(self, rel_path: str) -> bool:
        """Test a repository-relative POSIX file path using Rust semantics."""
        return self._matches([{"path": rel_path}])[0]
