"""0.0.2 #229: never mistake an invalid Git baseline for a new file."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from intentumdiff.sources.live_buffer_source import LiveBufferSource
from intentumdiff.vcs.git_cli import run_git_bytes


@pytest.fixture
def committed_repo(tmp_path):
    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout

    git("init", "-q")
    git("config", "user.name", "IntentumDiff Test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "été[1] file.py").write_text("original = 1\n", encoding="utf-8")
    git("add", "--", "été[1] file.py")
    git("commit", "-q", "-m", "Baseline")
    return tmp_path


def _read(repo, path: str, *, ref: str = "HEAD") -> str:
    source = LiveBufferSource(
        repo, path, "current = 2\n", ref=ref, cache_snapshot=True,
    )
    with patch(
        "intentumdiff.sources.live_buffer_source.resolve_repo_root",
        return_value=str(repo),
    ):
        return source.get_content()[0]


def test_existing_unicode_literal_path_preserves_exact_baseline(committed_repo):
    assert _read(committed_repo, "été[1] file.py") == "original = 1\n"


def test_truly_untracked_file_has_empty_old_side(committed_repo):
    assert _read(committed_repo, "new[1] file.py") == ""


def test_invalid_ref_refuses_review_instead_of_claiming_addition(committed_repo):
    with pytest.raises(subprocess.CalledProcessError):
        _read(committed_repo, "été[1] file.py", ref="unknown-intentumdiff-ref")


def test_unreadable_tracked_blob_refuses_review(committed_repo):
    def unreadable_blob(repo, args, **kwargs):
        if args[0] == "cat-file":
            raise subprocess.CalledProcessError(128, args)
        return run_git_bytes(repo, args, **kwargs)

    with patch(
        "intentumdiff.sources.live_buffer_source.resolve_repo_root",
        return_value=str(committed_repo),
    ), patch(
        "intentumdiff.sources.live_buffer_source.run_git_bytes",
        side_effect=unreadable_blob,
    ):
        source = LiveBufferSource(committed_repo, "été[1] file.py", "replacement")
        with pytest.raises(RuntimeError, match="Failed to read tracked Git blob"):
            source.get_content()


def test_invalid_utf8_tracked_blob_is_not_silently_replaced(committed_repo):
    """A successful Git blob read must preserve source bytes or fail closed."""
    name = "legacy_encoding.py"
    (committed_repo / name).write_bytes(b"\xffx = 1\n")
    subprocess.run(
        ["git", "-C", str(committed_repo), "add", "--", name],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(committed_repo), "commit", "-q", "-m", "Invalid UTF-8 baseline"],
        check=True, capture_output=True,
    )
    with pytest.raises(UnicodeDecodeError):
        _read(committed_repo, name)


def test_failed_independent_tree_inspection_is_not_an_addition(committed_repo):
    def git_failure(repo, args, **kwargs):
        raise subprocess.CalledProcessError(128, args)

    with patch(
        "intentumdiff.sources.live_buffer_source.resolve_repo_root",
        return_value=str(committed_repo),
    ), patch(
        "intentumdiff.sources.live_buffer_source.run_git_bytes",
        side_effect=git_failure,
    ):
        source = LiveBufferSource(committed_repo, "untracked.py", "replacement")
        with pytest.raises(subprocess.CalledProcessError):
            source.get_content()
