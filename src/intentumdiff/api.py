"""Thin public adapters for complete shared-engine operations."""
from __future__ import annotations
from intentumdiff.core.models import SemanticDiff


def review_text(old: str, new: str, *, filename: str = 'document.txt',
                new_filename: str | None = None) -> SemanticDiff:
    """Review plain text or Markdown without loading parser components.

    Use SemanticDiffer for language-aware parsing. Rust owns all comparison,
    Markdown reconciliation, source evidence, and lifecycle decisions here.
    """
    from intentumdiff.rust_core import _c_abi_call, _require_complete_batch_diff_dto

    # Rust owns the authoritative change inventory and semantic flags. Check
    # transport completeness before Pydantic can apply no-change defaults.
    response = _c_abi_call(
        'review_text', old, new, filename,
        filename if new_filename is None else new_filename,
    )
    return SemanticDiff.model_validate(_require_complete_batch_diff_dto(response))
