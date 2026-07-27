"""Integration tests for the `intentdiff watch` CLI subcommand (argument parsing only).

The actual file-watching loop (watchdog observer, background threads) is
exercised in ``tests/unit/test_watcher.py``.  These tests verify that the
CLI parser wires up the ``watch`` subcommand correctly.
"""
from __future__ import annotations


class TestWatchArgParsing:
    def test_default_paths_is_dot(self) -> None:
        """``intentdiff watch`` with no positionals defaults to ``['.']``."""
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch"])
        assert args.paths == ["."]

    def test_default_ref_is_head(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch"])
        assert args.ref == "HEAD"

    def test_default_debounce(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch"])
        assert args.debounce == 0.3

    def test_default_format_is_terminal(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch"])
        assert args.format == "terminal"

    def test_explicit_paths(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch", "src/", "tests/"])
        assert args.paths == ["src/", "tests/"]

    def test_explicit_ref(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch", "--ref", "main"])
        assert args.ref == "main"

    def test_explicit_debounce(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch", "--debounce", "0.5"])
        assert args.debounce == 0.5

    def test_explicit_format(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(["watch", "--format", "json"])
        assert args.format == "json"

    def test_combined_args(self) -> None:
        from intentdiff.cli import _build_parser

        args = _build_parser().parse_args(
            [
                "watch",
                "src/",
                "tests/",
                "--ref",
                "origin/main",
                "--debounce",
                "1.0",
                "--format",
                "json",
            ]
        )
        assert args.paths == ["src/", "tests/"]
        assert args.ref == "origin/main"
        assert args.debounce == 1.0
        assert args.format == "json"

    def test_func_is_cmd_watch(self) -> None:
        from intentdiff.cli import _build_parser, _cmd_watch

        args = _build_parser().parse_args(["watch"])
        assert args.func is _cmd_watch
