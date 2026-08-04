"""
intentumdiff.analysis.text_review
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The generic-text review WRAPPER. Parser token churn is replaced with stable
line/character spans by the Rust core (``try_rust_generic_text_review`` →
``text_review_generic.rs``), which is AUTHORITATIVE (readiness #90/#91) — there
is no Python engine fallback. Only the ``PresentationResult`` DTO and the thin
Rust wrapper survive here; the former Python mirror implementation was deleted
once the Rust generic-text review became the only engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intentumdiff.core.models import Change, ChangeGroup


@dataclass(frozen=True)
class PresentationResult:
    changes: list[Change]
    change_groups: list[ChangeGroup] = field(default_factory=list)
    ignored_style_changes: list[dict[str, Any]] = field(default_factory=list)


def normalize_generic_text_for_review(
    changes: list[Change],
    old_source: str,
    new_source: str,
) -> PresentationResult:
    """Replace generic parser token churn with line/character text spans.

    Rust-authoritative (readiness #90/#91): the Rust generic-text review is the
    only implementation. When it declines (backend unavailable, or the input
    exceeds its size cap), the changes pass through unchanged — "if Python didn't
    exist, IntentumDiff still works". The full port lives in ``text_review_generic.rs``.
    """
    from intentumdiff.rust_core import try_rust_generic_text_review

    rust_review = try_rust_generic_text_review(old_source, new_source, len(changes))
    if rust_review is not None:
        rust_changes, rust_groups = rust_review
        return PresentationResult(changes=rust_changes, change_groups=rust_groups)
    return PresentationResult(changes=changes)
