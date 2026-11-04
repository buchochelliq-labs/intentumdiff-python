"""Thin source adapter for Rust-owned unified patch reconstruction.

With original content, every hunk is validated at its declared position. Without
it, a regular modification can only reconstruct a contiguous excerpt, which is
explicitly warned and exposed through ``is_partial``. Set ``require_complete``
to reject excerpts. Unknown prefixes/gaps and multi-file patches are rejected.
"""
from __future__ import annotations

import json
import warnings
from typing import Any

from intentumdiff.sources.base import Source


class PatchExcerptWarning(UserWarning):
    """The patch does not establish complete original-file content."""


class PatchSource(Source):
    """Reconstruct one text file with the shared Rust engine.

    ``original_content`` provides the complete base. Without it, creation/deletion
    patches can establish complete content; regular patches yield an explicitly
    labelled excerpt. ``filename`` and ``language_hint`` are optional overrides.
    """
    def __init__(self, patch_text: str, original_content: str | None = None,
                 language_hint: str | None = None, filename: str | None = None,
                 *, require_complete: bool = False) -> None:
        self._request = {"patch_text": patch_text, "original_content": original_content,
                         "filename": filename, "require_complete": require_complete}
        self._language_hint = language_hint
        self._result: dict[str, Any] | None = None
        self._warned = False

    def _content(self) -> dict[str, Any]:
        if self._result is None:
            from intentumdiff.rust_core import _c_abi_call
            self._result = _c_abi_call("reconstruct_patch", json.dumps(self._request))
        return self._result

    @property
    def is_partial(self) -> bool:
        """Whether Rust identified the reconstructed content as an excerpt."""
        return self._content()["scope"] == "excerpt"

    def get_content(self) -> tuple[str, str, str, str | None]:
        result = self._content()
        if result["warning"] and not self._warned:
            warnings.warn(result["warning"], PatchExcerptWarning, stacklevel=2)
            self._warned = True
        return result["old_content"], result["new_content"], result["filename"], self._language_hint
