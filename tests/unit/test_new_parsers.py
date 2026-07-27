"""
tests/unit/test_new_parsers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for built-in parser registration and the Rust/Wasm FullParse
boundary. These tests run without compiled Wasm binaries.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest


class TestBuiltinEntryFunctions:
    """builtins.py functions return the right wasm filename."""

    @pytest.mark.parametrize(
        "fn_name, expected_stem",
        [
            ("java_parser_entry", "java_parser"),
            ("go_parser_entry", "go_parser"),
            ("rust_parser_entry", "rust_parser"),
            ("csharp_parser_entry", "csharp_parser"),
            ("ruby_parser_entry", "ruby_parser"),
            ("php_parser_entry", "php_parser"),
            ("kotlin_parser_entry", "kotlin_parser"),
            ("cpp_parser_entry", "cpp_parser"),
            ("swift_parser_entry", "swift_parser"),
            ("bash_parser_entry", "bash_parser"),
        ],
    )
    def test_entry_function_returns_correct_filename(self, fn_name, expected_stem):
        from intentdiff.plugins import builtins

        fn = getattr(builtins, fn_name)
        path = Path(fn())
        assert path.parent.name == "wasm"
        assert path.stem == expected_stem
        assert path.suffix == ".wasm"

    def test_all_wasm_paths_share_same_directory(self):
        from intentdiff.plugins import builtins

        fns = [
            builtins.java_parser_entry,
            builtins.go_parser_entry,
            builtins.rust_parser_entry,
            builtins.csharp_parser_entry,
            builtins.ruby_parser_entry,
            builtins.php_parser_entry,
            builtins.kotlin_parser_entry,
            builtins.cpp_parser_entry,
            builtins.swift_parser_entry,
            builtins.bash_parser_entry,
        ]
        dirs = {Path(fn()).parent for fn in fns}
        assert len(dirs) == 1
        assert next(iter(dirs)).name == "wasm"

    def test_cpp_entry_also_used_for_c(self):
        from intentdiff.plugins import builtins

        assert Path(builtins.cpp_parser_entry()).name == "cpp_parser.wasm"


class TestFullParseBoundary:
    """The Python shell no longer hosts Tree-sitter parsing."""

    def test_full_parse_parser_receives_raw_source(self):
        from intentdiff.differ import SemanticDiffer

        class Parser:
            grammar_id = "example"
            parser_mode = "full-parse"

        differ = SemanticDiffer()
        assert differ._parse("let x = 1", Parser(), "javascript", "x.js") == "let x = 1"

    def test_interpret_cst_parser_is_rejected_actionably(self):
        from intentdiff.differ import SemanticDiffer
        from intentdiff.plugins.exceptions import PluginNotFoundError

        class Parser:
            grammar_id = "legacy"
            parser_mode = "interpret-cst"

        differ = SemanticDiffer()
        with pytest.raises(PluginNotFoundError, match="FullParse Rust/Wasm plugin"):
            differ._parse("let x = 1", Parser(), "javascript", "x.js")


class TestExtensionMapping:
    """
    The ``pyproject.toml`` entry-points cover the expected file extensions.

    This requires the package to be installed in development mode. The test is
    skipped if the package metadata is not visible in the active environment.
    """

    @pytest.fixture(autouse=True)
    def _check_installed(self):
        try:
            importlib.metadata.distribution("intentdiff")
        except importlib.metadata.PackageNotFoundError:
            pytest.skip("intentdiff not installed; skipping entry-point tests")
        eps = {ep.name for ep in importlib.metadata.entry_points(group="intentdiff.parsers")}
        if "java" not in eps:
            pytest.skip("Parser entry-points not registered; run local sync first")

    def test_all_core_grammars_registered(self):
        eps = importlib.metadata.entry_points(group="intentdiff.parsers")
        names = {ep.name for ep in eps}
        expected = {
            "java",
            "go",
            "rust",
            "csharp",
            "ruby",
            "php",
            "kotlin",
            "c",
            "cpp",
            "swift",
            "bash",
        }
        missing = expected - names
        assert not missing, f"Missing entry-points: {missing}"

    def test_c_and_cpp_point_to_same_entry(self):
        eps = {
            ep.name: ep
            for ep in importlib.metadata.entry_points(group="intentdiff.parsers")
        }
        assert "c" in eps
        assert "cpp" in eps
        assert "cpp_parser_entry" in eps["c"].value
        assert "cpp_parser_entry" in eps["cpp"].value
