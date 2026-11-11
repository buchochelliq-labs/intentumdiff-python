"""CLI argument validation: bad input is an argparse usage error (exit 2) before any engine work."""

from __future__ import annotations

import pytest

from intentumdiff.cli import _build_parser


def _parse(*args: str):
    return _build_parser().parse_args(list(args))


def _usage_error(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    with pytest.raises(SystemExit) as exc_info:
        _parse(*args)
    assert exc_info.value.code == 2
    return capsys.readouterr().err


# --- #315: image budgets -----------------------------------------------------------------


_ASSET_DIFF = ("assets", "diff", "--before", "a.png", "--after", "b.png")


@pytest.mark.parametrize(
    "value", ["nan", "NaN", "inf", "-inf", "Infinity", "1e309", "0", "-1", "abc", ""]
)
def test_nonfinite_or_invalid_megapixels_are_usage_errors(capsys, value) -> None:
    err = _usage_error(capsys, *_ASSET_DIFF, f"--max-decoded-megapixels={value}")
    assert f"invalid megapixel value {value!r}: use a finite positive number" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    ("value", "pixels"), [("0.01", 10_000), ("2.5", 2_500_000), ("1e-9", 1), ("40", 40_000_000)]
)
def test_valid_megapixels_keep_exact_units(value, pixels) -> None:
    args = _parse(*_ASSET_DIFF, "--max-decoded-megapixels", value)
    assert args.max_decoded_pixels == pixels


# --- #318: git positionals ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("positionals", "repo", "file"),
    [((), ".", None), (("f.py",), ".", "f.py"), (("repo", "f.py"), "repo", "f.py")],
)
def test_git_documented_positionals(positionals, repo, file) -> None:
    args = _parse("git", *positionals)
    assert (args.repo, args.file) == (repo, file)


@pytest.mark.parametrize("extra", [("ignored.py",), ("a.py", "b.py")])
def test_git_rejects_extra_positionals_instead_of_dropping_them(capsys, extra) -> None:
    err = _usage_error(capsys, "git", "repo", "f.py", *extra, "--json")
    assert "at most two positionals" in err
    assert f"got extra {list(extra)}" in err
