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


def test_catfile_batch_rejects_invalid_utf8_in_source_blob() -> None:
    source = b"\xffx = 1\n"
    with pytest.raises(RuntimeError, match=r"Invalid UTF-8 in Git blob deadbeef"):
        _parse_catfile_output(_blob_response("deadbeef", source))


def test_catfile_uses_rust_classifier_for_binary_without_nul(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recognized PDFs are not malformed source just because they contain 0xff."""
    from intentumdiff import content_type

    pdf = b"%PDF-1.7\\n%\\xff\\xfe\\n%%EOF\\n"
    source = "name = 'caf\\u00e9'\\n".encode("utf-8")
    observed: list[bytes] = []

    def rust_detector(original: bytes) -> dict[str, object]:
        observed.append(original)
        if original.startswith(b"%PDF-"):
            return {"is_text": False, "mime": "application/pdf"}
        return {"is_text": True, "mime": "text/plain"}

    monkeypatch.setattr(content_type, "detect_content_type", rust_detector)
    response = _blob_response("pdfsha", pdf) + _blob_response("srcsha", source)
    assert _parse_catfile_output(response) == {
        "pdfsha": None,
        "srcsha": "name = 'caf\\u00e9'\\n",
    }
    assert observed == [pdf, source]


def test_catfile_opaque_invalid_utf8_is_not_silently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rust's unknown-binary response must not excuse malformed source."""
    from intentumdiff import content_type

    monkeypatch.setattr(
        content_type,
        "detect_content_type",
        lambda _raw: {"is_text": False, "mime": "application/octet-stream"},
    )
    with pytest.raises(RuntimeError, match="Invalid UTF-8 in Git blob source"):
        _parse_catfile_output(_blob_response("source", b"value = \\xff\\n"))


def test_catfile_detector_failure_is_not_a_successful_binary_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Rust engine never authorizes a partially indexed Git tree."""
    from intentumdiff import content_type

    def unavailable(_original: bytes) -> dict[str, object]:
        raise RuntimeError("Rust content detector unavailable")

    monkeypatch.setattr(content_type, "detect_content_type", unavailable)
    with pytest.raises(RuntimeError, match="Rust content detector unavailable"):
        _parse_catfile_output(_blob_response("source", b"answer = 42\\n"))


def test_real_git_collector_skips_pdf_without_nul_and_retains_source(
    tmp_path: "Path",
) -> None:
    """Use real git cat-file output and Rust's PDF magic-byte classifier."""
    import subprocess
    from pathlib import Path

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*argv: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *argv],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    git("init")
    git("config", "user.name", "IntentumDiff Test")
    git("config", "user.email", "test@example.invalid")
    (repo / "source.py").write_bytes("result = 'caf\\u00e9'\\n".encode("utf-8"))
    pdf = b"%PDF-1.7\\n%\\xff\\xfe\\n1 0 obj\\n<< /Type /Catalog >>\\nendobj\\n%%EOF\\n"
    assert b"\\x00" not in pdf
    (repo / "report.pdf").write_bytes(pdf)
    git("add", ".")
    git("commit", "-m", "source plus non-NUL binary")

    files, ignored = _collect_blob_files(str(repo), "HEAD", None)
    assert files == [("source.py", "result = 'caf\\u00e9'\\n")]
    assert ignored == []
