"""Rust-engine certification gate helpers for intentumdiff.differ."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

STRICT_RUST_GATE_ENV = "INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE"


def _validate_git_ref(ref: str) -> None:
    """Reject a git ref that could be mis-read as a ``git`` command-line option
    (argument injection — e.g. ``--output=<path>`` turns ``git diff <ref>`` into
    an arbitrary file write) or inject extra ``cat-file --batch`` request lines.

    A ref starting with ``-`` is invalid per git's own ref-format rules, so this
    never rejects a legitimate ref. The empty ref (working tree) is allowed.
    This guards the public ``diff_commit`` boundary; the Rust native path and the
    GitPython path both consume refs downstream. Security review: issue #88.
    """
    if not ref:
        return
    if ref.startswith("-"):
        raise ValueError(
            f"Invalid git ref {ref!r}: must not start with '-' "
            "(argument-injection guard)."
        )
    if any(ch in ref for ch in ("\n", "\r", "\0")):
        raise ValueError(
            f"Invalid git ref {ref!r}: must not contain a newline or NUL."
        )


# Languages whose single-file live diffs are served by the CERTIFIED Rust batch path.
# This set grows one ported slice at a time (migration plan Phase 2, issue #40) — adding a
# language here asserts the Rust core reproduces its full acceptance contract.
# INTENTUMDIFF_FORCE_RUST_CERTIFIED (comma-separated language ids) force-adds languages during
# port development: combined with INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE=1, the raised gate
# errors for a forced language are that port's concrete checklist.
RUST_CERTIFIED_LANGUAGES: frozenset[str] = frozenset({"python"})


def _rust_certified_languages() -> frozenset[str]:
    forced = os.environ.get("INTENTUMDIFF_FORCE_RUST_CERTIFIED", "")
    if not forced:
        return RUST_CERTIFIED_LANGUAGES
    return RUST_CERTIFIED_LANGUAGES | {
        part.strip().lower() for part in forced.split(",") if part.strip()
    }


def _strict_rust_only_enabled() -> bool:
    return os.getenv(STRICT_RUST_GATE_ENV, "").strip() == "1"


class RustOnlyGateError(RuntimeError):
    """Raised when the RUST_ONLY gate blocks a genuine token-level fallback.

    Subclasses ``RuntimeError`` so existing ``pytest.raises(RuntimeError, …)``
    assertions still match, but its distinct type lets the commit-path fan-out
    (``diff_commit``'s per-file executor) tell a fatal gate trip apart from the
    ordinary per-file ``RuntimeError`` it deliberately swallows-and-skips. A
    swallowed gate error would silently drop a file instead of failing loudly,
    defeating the whole point of the gate — so the executor re-raises this type.
    """


def _raise_rust_only_gate_error(reason: str | None) -> None:
    if reason is not None and _strict_rust_only_enabled():
        raise RustOnlyGateError(f"Rust-only engine gate prevented fallback: {reason}")


