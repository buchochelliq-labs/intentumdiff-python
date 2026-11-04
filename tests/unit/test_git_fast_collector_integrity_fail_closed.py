"""Fast Git source collection cannot manufacture absent baselines or hide read errors.

Source-judged host-I/O regressions for Python #241 and #242. No Rust semantic
implementation is duplicated here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intentumdiff.sources import git_source


def _mock_batch(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    def fake_git(root: str, args: list[str], **kwargs: object) -> bytes:
        assert args == ["cat-file", "--batch"]
        assert kwargs["input_bytes"] == b"HEAD:old.py\n"
        return data

    monkeypatch.setattr(git_source, "run_git_bytes", fake_git)


def test_fast_batch_accepts_complete_tracked_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_batch(monkeypatch, b"abc blob 6\nx = 1\n\n")
    assert git_source._read_commit_paths_batch("/repo", "HEAD", ["old.py"]) == {
        "old.py": "x = 1\n"
    }


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (b"HEAD:old.py missing\n", "Missing tracked Git baseline"),
        (b"abc tree 6\nx = 1\n\n", "malformed git cat-file batch blob header"),
        (b"abc blob nope\nx = 1\n\n", "malformed git cat-file batch size"),
        (b"abc blob -1\n", "incomplete git cat-file batch blob payload"),
        (b"abc blob 6\nx = 1\n", "incomplete git cat-file batch blob payload"),
        (b"abc blob 6\nx = 1\n\nextra", "Unexpected extra"),
        (b"", "malformed git cat-file batch output"),
    ],
)
def test_fast_batch_rejects_missing_malformed_or_truncated_baseline(
    monkeypatch: pytest.MonkeyPatch, response: bytes, message: str
) -> None:
    _mock_batch(monkeypatch, response)
    with pytest.raises(RuntimeError, match=message):
        git_source._read_commit_paths_batch("/repo", "HEAD", ["old.py"])


def test_untracked_read_failure_is_not_a_binary_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed file becoming unreadable aborts rather than yielding a partial set."""
    src = tmp_path / "new.py"
    src.write_text("changed = True\n", encoding="utf-8")
    monkeypatch.setattr(git_source, "resolve_repo_root", lambda repo: str(tmp_path))

    def fake_git(root: str, args: list[str], **kwargs: object) -> bytes:
        if args[0] == "diff" and "--name-status" in args:
            return b""
        if args[0] == "ls-files":
            return b"new.py\0"
        if args[0] == "diff" and "--name-only" in args:
            return b""
        raise AssertionError(args)

    monkeypatch.setattr(git_source, "run_git_bytes", fake_git)
    original_read_bytes = Path.read_bytes

    def fail_enumerated_source(path: Path) -> bytes:
        if path.name == "new.py":
            raise PermissionError("controlled read denial")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_enumerated_source)
    with pytest.raises(RuntimeError, match="Failed to read untracked working-tree source") as error:
        git_source.collect_working_tree_python_sources_fast(tmp_path, "HEAD")
    assert isinstance(error.value.__cause__, PermissionError)
