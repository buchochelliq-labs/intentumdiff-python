"""Fail-closed Git batch ingestion: host-I/O must never manufacture a complete index."""

from __future__ import annotations

import pytest

from intentumdiff.core.indexer import _collect_blob_files, _parse_catfile_output


def _blob_response(sha: str, content: bytes) -> bytes:
    return f"{sha} blob {len(content)}\n".encode("ascii") + content + b"\n"


def test_catfile_batch_accepts_complete_text_and_intentionally_skips_binary() -> None:
    output = _blob_response("abc", b"answer = 42\n") + _blob_response("def", b"\x00binary")
    assert _parse_catfile_output(output) == {
        "abc": "answer = 42\n",
        "def": None,
    }


@pytest.mark.parametrize(
    "batch,diagnostic",
    [
        (b"deadbeef missing\n", "is missing"),
        (b"deadbeef blob 4\nabc\n", "Truncated"),
        (b"deadbeef blob 3\nab", "Truncated"),
        (b"deadbeef blob nope\nabc\n", "Invalid"),
        (b"deadbeef blob", "Incomplete"),
        (b"deadbeef tree 3\nabc\n", "Unexpected"),
        (_blob_response("deadbeef", b"a") * 2, "Duplicate"),
    ],
)
def test_catfile_batch_rejects_incomplete_or_malformed_responses(
    batch: bytes, diagnostic: str
) -> None:
    with pytest.raises(RuntimeError, match=diagnostic):
        _parse_catfile_output(batch)


@pytest.mark.parametrize("batch", [b"", b"deadbeef missing\n"])
def test_collector_rejects_missing_requested_blob(
    monkeypatch: pytest.MonkeyPatch, batch: bytes
) -> None:
    def fake_git(_root: str, argv: list[str], **kwargs: object) -> bytes:
        if argv[:1] == ["ls-tree"]:
            return b"100644 blob deadbeef\tfile.py\x00"
        if argv[:1] == ["cat-file"]:
            assert kwargs.get("input_bytes") == b"deadbeef"
            return batch
        raise AssertionError(argv)

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    with pytest.raises(RuntimeError, match="missing"):
        _collect_blob_files("/not-needed", "HEAD", None)


def test_collector_retains_shared_text_paths_but_skips_known_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(_root: str, argv: list[str], **kwargs: object) -> bytes:
        if argv[:1] == ["ls-tree"]:
            return (
                b"100644 blob abc\ta.py\x00"
                b"100644 blob abc\tb.py\x00"
                b"100644 blob def\tpicture.dat\x00"
            )
        if argv[:1] == ["cat-file"]:
            assert kwargs.get("input_bytes") == b"abc\ndef"
            return _blob_response("abc", b"x = 1\n") + _blob_response("def", b"\x00binary")
        raise AssertionError(argv)

    monkeypatch.setattr("intentumdiff.vcs.git_cli.run_git_bytes", fake_git)
    files, ignored = _collect_blob_files("/not-needed", "HEAD", None)
    assert ignored == []
    assert files == [("a.py", "x = 1\n"), ("b.py", "x = 1\n")]
