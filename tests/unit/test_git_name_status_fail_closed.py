"""Git -z inventories must be complete and preserve filename bytes (#239/#240)."""

from __future__ import annotations

import pytest

from intentumdiff.sources import git_source
from intentumdiff.vcs.git_cli import parse_name_status_z


NUL = bytes([0])


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", []),
        (b"M" + NUL + b"sample.py" + NUL, [("M", "sample.py", "sample.py")]),
        (
            b"R100" + NUL + b"old.py" + NUL + b"new.py" + NUL,
            [("R", "old.py", "new.py")],
        ),
        (
            b"C75" + NUL + b"old.py" + NUL + b"new.py" + NUL,
            [("C", "old.py", "new.py")],
        ),
        (
            b"A" + NUL + "café.py".encode("utf-8") + NUL,
            [("A", "café.py", "café.py")],
        ),
        (
            b"M" + NUL + b"first.py" + NUL + b"D" + NUL + b"second.py" + NUL,
            [
                ("M", "first.py", "first.py"),
                ("D", "second.py", "second.py"),
            ],
        ),
    ],
)
def test_shared_name_status_parser_preserves_complete_inventory(payload, expected):
    assert parse_name_status_z(payload) == expected
    # Legacy callers in GitSource and commit review use precisely this parser.
    assert git_source._parse_name_status_z is parse_name_status_z


@pytest.mark.parametrize(
    ("payload", "exception", "message"),
    [
        (b"M" + NUL + b"file.py", RuntimeError, "missing NUL terminator"),
        (b"M" + NUL, RuntimeError, "Truncated"),
        (b"R100" + NUL + b"old.py" + NUL, RuntimeError, "Truncated"),
        (b"M" + NUL + NUL, RuntimeError, "Empty filename"),
        (NUL + b"file.py" + NUL, RuntimeError, "Invalid"),
        (bytes([0xFF]) + NUL + b"x.py" + NUL, UnicodeError, "Invalid ASCII"),
        (
            b"M" + NUL + b"f-" + bytes([0xFF]) + b".py" + NUL,
            UnicodeError,
            "Invalid UTF-8",
        ),
        (b"Rbogus" + NUL + b"a.py" + NUL + b"b.py" + NUL, RuntimeError, "Invalid"),
    ],
)
def test_shared_name_status_parser_never_returns_partial_or_lossy_inventory(
    payload, exception, message
):
    with pytest.raises(exception, match=message):
        parse_name_status_z(payload)


@pytest.mark.parametrize(
    ("payload", "exception", "message"),
    [
        (b"file.py", RuntimeError, "missing NUL terminator"),
        (b"a.py" + NUL + NUL, RuntimeError, "Empty path"),
        (
            b"bad-" + bytes([0xFE]) + b".py" + NUL,
            UnicodeError,
            "Invalid UTF-8",
        ),
    ],
)
def test_untracked_and_staged_path_decoder_rejects_corruption(
    payload, exception, message
):
    with pytest.raises(exception, match=message):
        git_source._decode_nul_paths(payload, source="git ls-files -z")


def test_git_path_decoder_preserves_valid_unicode_and_order():
    payload = "café.py".encode("utf-8") + NUL + "東京.py".encode("utf-8") + NUL
    assert git_source._decode_nul_paths(payload, source="git ls-files -z") == [
        "café.py", "東京.py"
    ]


def test_fast_collector_declines_unsupported_status_instead_of_omitting_source(
    monkeypatch,
):
    monkeypatch.setattr(git_source, "resolve_repo_root", lambda repo: "/repo")

    def fake_git(root, argv, **kwargs):
        assert argv[:2] == ["diff", "--name-status"]
        return b"T" + NUL + b"changed.py" + NUL

    monkeypatch.setattr(git_source, "run_git_bytes", fake_git)
    source, reason = git_source.collect_working_tree_python_sources_fast("/repo", "HEAD")
    assert source == []
    assert reason is not None and "Git change status" in reason


def test_fast_collector_rejects_truncated_status_without_certified_decline(
    monkeypatch,
):
    monkeypatch.setattr(git_source, "resolve_repo_root", lambda repo: "/repo")
    monkeypatch.setattr(
        git_source, "run_git_bytes",
        lambda root, args, **kwargs: b"M" + NUL + b"changed.py",
    )
    with pytest.raises(RuntimeError, match="missing NUL terminator"):
        git_source.collect_working_tree_python_sources_fast("/repo", "HEAD")
