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

    def display_names(self) -> tuple[str, str] | None:
        """
        Distinct ``(old, new)`` names, when the two sides are named differently.

        ``get_content`` returns ONE filename because that is what language detection
        needs, and for most sources it is also the right thing to display: a git diff
        compares one path at two revisions, so both sides share a name.

        ``FileSource`` is the exception — it compares two separately named files — and
        collapsing both sides onto the new name made the CLI report that it had diffed
        a file with itself.  Sources that know two names override this; ``None`` means
        "one name applies to both", which stays the default.
        """
        return None
