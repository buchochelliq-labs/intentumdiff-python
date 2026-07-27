"""
intentdiff.sources.string_source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Compare two in-memory strings — useful for diffing LLM-generated code or
test fixtures.
"""

from __future__ import annotations

from intentdiff.sources.base import Source


class StringSource(Source):
    """
    Compare two in-memory source strings.

    Parameters
    ----------
    old_content:
        The old (before) version of the source code.
    new_content:
        The new (after) version.
    filename:
        Logical filename used for language detection and diff output.
    language_hint:
        Optional explicit language.  If omitted, the plugin registry infers it
        from ``filename``.
    """

    def __init__(
        self,
        old_content: str,
        new_content: str,
        filename: str,
        language_hint: str | None = None,
    ) -> None:
        self._old = old_content
        self._new = new_content
        self._filename = filename
        self._language_hint = language_hint

    def get_content(self) -> tuple[str, str, str, str | None]:
        return self._old, self._new, self._filename, self._language_hint
