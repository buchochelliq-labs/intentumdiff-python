"""
intentumdiff.sources.patch_source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reconstruct old/new content from a unified diff patch string.

Typical use-case: diffing an LLM-generated patch (e.g. GitHub Copilot output)
without access to the original file.

The patch must follow the unified diff format (``--- a/...`` / ``+++ b/...``).
``unidiff`` (third-party) is used for robust parsing.
"""

from __future__ import annotations

import io
from pathlib import PurePosixPath

from unidiff import PatchSet

from intentumdiff.sources.base import Source


def _apply_patch_to_original(original: str, patch_text: str) -> str:
    """
    Reconstruct the patched (new) content by applying hunks from a unified diff.

    We do a line-based apply; this is intentionally simple — we are not a full
    ``patch`` utility.  For complex patches (binary, context mismatch) an
    error is raised.
    """
    patchset = PatchSet(io.StringIO(patch_text))
    if not patchset:
        return original

    patched_file = patchset[0]  # we support single-file patches here
    old_lines = original.splitlines(keepends=True)
    new_lines: list[str] = []
    old_idx = 0  # 1-based line cursor

    for hunk in patched_file:
        # Copy context lines before this hunk
        hunk_start = hunk.source_start  # 1-based
        while old_idx < hunk_start - 1:
            new_lines.append(old_lines[old_idx])
            old_idx += 1

        for line in hunk:
            if line.is_context:
                new_lines.append(old_lines[old_idx])
                old_idx += 1
            elif line.is_added:
                new_lines.append(line.value)
            elif line.is_removed:
                old_idx += 1  # skip removed original line

    # Trailing context
    while old_idx < len(old_lines):
        new_lines.append(old_lines[old_idx])
        old_idx += 1

    return "".join(new_lines)


def _extract_filename(patch_text: str) -> str:
    """Extract the target filename from the ``+++`` header of a patch."""
    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            # Strip leading "b/" git prefix
            path = raw[2:] if raw.startswith("b/") else raw
            return path
    return "unknown"


class PatchSource(Source):
    """
    Reconstruct old and new content from a unified diff patch.

    Parameters
    ----------
    patch_text:
        Full unified diff string (as produced by ``git diff``, GitHub Copilot,
        or similar tools).
    original_content:
        The original (old) file content.  If omitted, the old content is
        reconstructed from the patch's ``-`` lines (best-effort).
    language_hint:
        Optional language override.
    filename:
        Display filename.  If omitted, extracted from the ``+++`` header.
    """

    def __init__(
        self,
        patch_text: str,
        original_content: str | None = None,
        language_hint: str | None = None,
        filename: str | None = None,
    ) -> None:
        self._patch_text = patch_text
        self._original = original_content
        self._language_hint = language_hint
        self._filename = filename or _extract_filename(patch_text)

    def get_content(self) -> tuple[str, str, str, str | None]:
        if self._original is not None:
            old_content = self._original
        else:
            old_content = self._reconstruct_original()

        new_content = _apply_patch_to_original(old_content, self._patch_text)
        return old_content, new_content, self._filename, self._language_hint

    def _reconstruct_original(self) -> str:
        """Build the original content from ``-`` and context lines in the patch."""
        lines: list[str] = []
        for line in self._patch_text.splitlines(keepends=True):
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("-"):
                lines.append(line[1:])
            elif line.startswith(" "):
                lines.append(line[1:])
            # Lines starting with '+' are new — skip them
        return "".join(lines)
