"""Reject incomplete Git tree listings before a source index can be certified."""

from __future__ import annotations

import pytest

from intentumdiff.core.indexer import _collect_blob_files


@pytest.mark.parametrize(
    "tree,diagnostic",
    [
        (b"100644 blob abc\tfile.py", "missing NUL"),
        (b"100644 blob abc\x00", "Malformed"),
        (b"100644 blob abc\t\x00", "Malformed"),
        (b"100644 blob\tfile.py\x00", "Malformed"),
        (b"nonsense\x00", "Malformed"),
        (b"100644 tree abc\tdir\x00", "Unexpected"),
        (b"100644 blob abc\tgood.py\x00broken", "missing NUL"),
    ],
)
def test_collector_rejects_incomplete_or_malformed_tree_inventory(
    monkeypatch: pytest.MonkeyPatch, tree: bytes, diagnostic: str,
) -> None:
    calls = []

    def fake_git(root: str, argv: list[str], **kwargs: object) -> bytes:
        calls.append(argv)
        assert argv[:1] == ["ls-tree"]
        return tree

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    with pytest.raises(RuntimeError, match=diagnostic):
        _collect_blob_files("/unused", "HEAD", None)
    assert calls == [["ls-tree", "-r", "-z", "HEAD"]]


def test_valid_empty_tree_does_not_require_cat_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(root: str, argv: list[str], **kwargs: object) -> bytes:
        assert argv[:1] == ["ls-tree"]
        return b""

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    assert _collect_blob_files("/unused", "HEAD", None) == ([], [])


def test_valid_gitlink_is_intentionally_excluded_but_blobs_are_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(root: str, argv: list[str], **kwargs: object) -> bytes:
        if argv[:1] == ["ls-tree"]:
            return (
                b"160000 commit submodule\tvendor/submodule\x00"
                b"100644 blob abc\tfile.py\x00"
            )
        assert argv[:1] == ["cat-file"]
        assert kwargs.get("input_bytes") == b"abc"
        return b"abc blob 6\nx = 1\n\n"

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    assert _collect_blob_files("/unused", "HEAD", None) == (
        [("file.py", "x = 1\n")], []
    )
