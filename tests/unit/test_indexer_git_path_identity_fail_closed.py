"""Git source-tree path identity is byte-sensitive: never substitute U+FFFD (#238)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from intentumdiff.core.indexer import _collect_blob_files


@pytest.mark.parametrize("invalid_byte", [0xFF, 0xFE])
@pytest.mark.parametrize("ignore", [False, True])
def test_invalid_utf8_git_tree_path_fails_before_indexing(
    monkeypatch: pytest.MonkeyPatch, invalid_byte: int, ignore: bool
) -> None:
    """Distinct undecodable Git paths must not collapse into one invented path."""
    path = b"sample-" + bytes([invalid_byte]) + b".py"
    tree = b"100644 blob abc\t" + path + bytes([0])
    calls: list[list[str]] = []

    def fake_git(root: str, argv: list[str], **kwargs: object) -> bytes:
        calls.append(argv)
        assert argv[0] == "ls-tree"
        return tree

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    diffignore = SimpleNamespace(is_ignored=lambda path: True) if ignore else None

    with pytest.raises(RuntimeError, match="Invalid UTF-8 in git ls-tree path"):
        _collect_blob_files("/unused", "HEAD", diffignore)
    assert calls == [["ls-tree", "-r", "-z", "HEAD"]]


def test_invalid_object_id_is_rejected_before_cat_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = b"100644 blob abc" + bytes([0xFF]) + b"\tvalid.py" + bytes([0])
    calls = []

    def fake_git(root: str, argv: list[str], **kwargs: object) -> bytes:
        calls.append(argv)
        assert argv[0] == "ls-tree"
        return tree

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    with pytest.raises(RuntimeError, match="Invalid ASCII object ID"):
        _collect_blob_files("/unused", "HEAD", None)
    assert calls == [["ls-tree", "-r", "-z", "HEAD"]]


@pytest.mark.parametrize("name", ["café.py", "東京.py"])
def test_valid_unicode_git_path_preserves_exact_identity(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    tree = b"100644 blob abc\t" + name.encode("utf-8") + bytes([0])

    def fake_git(root: str, argv: list[str], **kwargs: object) -> bytes:
        if argv[0] == "ls-tree":
            return tree
        assert argv[0] == "cat-file"
        assert kwargs["input_bytes"] == b"abc"
        return b"abc blob 6\nx = 1\n\n"

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    assert _collect_blob_files("/unused", "HEAD", None) == (
        [(name, "x = 1\n")], []
    )
