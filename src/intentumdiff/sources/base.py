"""
intentumdiff.sources.base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Abstract base class for all input sources.

Every Source produces a 4-tuple:
  (old_content: str, new_content: str, filename: str, language_hint: str | None)

``language_hint`` may be None — the plugin registry will detect the language.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Source(ABC):
    """Abstract input source.  Subclasses must implement ``get_content``."""

    @abstractmethod
    def get_content(self) -> tuple[str, str, str, str | None]:
        """
        Return ``(old_content, new_content, filename, language_hint)``.

        ``language_hint`` — e.g. "python", "sql".  Pass None to auto-detect.
        """
        ...
