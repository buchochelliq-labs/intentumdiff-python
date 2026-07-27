"""
intentdiff.core.diffignore
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
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import pathspec

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

    import pathspec  # lazy — keep import-time fast when diffignore is absent

    spec = pathspec.PathSpec.from_lines("gitignore", text.splitlines())
    logger.debug("Loaded .diffignore from %s (%d pattern(s))", path, len(spec.patterns))
    return DiffIgnore(spec)


class DiffIgnore:
    """
    Wraps a ``pathspec.PathSpec`` to test whether relative file paths should
    be excluded from the diff / index pipeline.

    Instances are created by :func:`load_diffignore`; construct one directly
    only in tests.
    """

    __slots__ = ("_spec",)

    def __init__(self, spec: "pathspec.PathSpec") -> None:
        self._spec = spec

    def is_ignored(self, rel_path: str) -> bool:
        """
        Return ``True`` if *rel_path* is matched by any pattern in the file.

        *rel_path* must use **forward slashes** (POSIX style), e.g.
        ``"src/generated/foo.py"`` — not ``"src\\generated\\foo.py"``.
        """
        return bool(self._spec.match_file(rel_path))
