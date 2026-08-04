"""Git argument-injection guard (security review #88, boundary 2).

A git ref flows from ``diff_commit`` into ``git diff <ref>`` (Rust native path)
and the ``cat-file --batch`` request. Without validation, a ref beginning with
``-`` is parsed by git as an option — ``--output=<path>`` turns the diff into an
arbitrary file write — and a ref with a newline injects extra batch requests.
The extension's ``intentumdiff.ref`` setting is window-scoped (workspace-settable),
so a hostile repo's ``.vscode/settings.json`` is a plausible source of the ref.

These pins assert the public ``diff_commit`` boundary rejects such refs before
they reach either engine path. No git repo or subprocess is exercised — the
guard runs before any git call.
"""

from __future__ import annotations

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff._differ_gate import _validate_git_ref


@pytest.mark.parametrize(
    "ref",
    [
        "--output=/tmp/pwned",
        "-O/tmp/x",
        "--upload-pack=evil",
        "HEAD\n--output=x",
        "HEAD\r",
        "HEAD\x00",
    ],
)
def test_validate_git_ref_rejects_injection(ref: str) -> None:
    with pytest.raises(ValueError, match="Invalid git ref"):
        _validate_git_ref(ref)


@pytest.mark.parametrize("ref", ["", "HEAD", "origin/main", "v1.2.3", "a1b2c3d4"])
def test_validate_git_ref_accepts_legitimate_refs(ref: str) -> None:
    _validate_git_ref(ref)  # no raise


def test_diff_commit_rejects_option_like_ref(tmp_path) -> None:
    """The guard fires at the public boundary, before any git subprocess."""
    with pytest.raises(ValueError, match="Invalid git ref"):
        SemanticDiffer().diff_commit(str(tmp_path), old_ref="--output=/tmp/x")


def test_diff_commit_rejects_option_like_new_ref(tmp_path) -> None:
    with pytest.raises(ValueError, match="Invalid git ref"):
        SemanticDiffer().diff_commit(str(tmp_path), old_ref="HEAD", new_ref="-O/x")
