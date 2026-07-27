"""
tests/unit/test_full_parse_mode.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for the ``full-parse`` plugin mode path in ``SemanticDiffer._run_pipeline``.

Before the bug fix, trivia stripping and the style-only hash shortcut both
called ``json.loads(cst_json)`` on what was actually raw source text for
full-parse plugins, causing a ``json.JSONDecodeError`` crash.

All tests here run without wasmtime — the plugin is fully mocked.
"""

from __future__ import annotations

import hashlib
import json
import os
from unittest.mock import MagicMock

import pytest

from intentdiff.core.models import (
    ChangeType,
    DiffConfig,
    NodePosition,
    SemanticDiff,
    SemanticNode,
)
from intentdiff.differ import SemanticDiffer
from intentdiff.plugins.adapter import ParserAdapter
from intentdiff.plugins.registry import PluginRegistry

# Every test in this module drives a fully MOCKED ParserAdapter (see module docstring —
# "the plugin is fully mocked"): `process()` returns hand-built trees and no real Rust
# engine runs. Under INTENTDIFF_ENFORCE_RUST_ONLY_ENGINE the gate requires the native
# Rust path, which a mock cannot satisfy, so these plumbing tests (raw-source handling,
# CST size-guard, no-crash) are skipped in that mode. Real full-parse Wasm parsers route
# through the Rust finalize path and are covered by their own per-language suites.
pytestmark = pytest.mark.skipif(
    os.getenv("INTENTDIFF_ENFORCE_RUST_ONLY_ENGINE") == "1",
    reason="full-parse plumbing tests use a mocked parser; RUST_ONLY requires the native Rust path",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(sl: int = 0, el: int = 1) -> NodePosition:
    return NodePosition(start_line=sl, start_col=0, end_line=el, end_col=0)


def _leaf_node(node_id: str, label: str) -> SemanticNode:
    h = hashlib.sha256(f"identifier:{label}".encode()).hexdigest()
    return SemanticNode(
        id=node_id,
        node_type="identifier",
        label=label,
        position=_pos(),
        structural_hash=h,
    )


def _module_node(node_id: str, children: list[SemanticNode]) -> SemanticNode:
    child_hashes = "|".join(c.structural_hash for c in children)
    h = hashlib.sha256(f"module|{child_hashes}".encode()).hexdigest()
    return SemanticNode(
        id=node_id,
        node_type="module",
        label="module",
        position=_pos(0, 5),
        structural_hash=h,
        children=children,
    )


def _make_full_parse_adapter(tree: SemanticNode) -> ParserAdapter:
    """Return a ParserAdapter mock that reports full-parse mode."""
    plugin = MagicMock()
    adapter = MagicMock(spec=ParserAdapter)
    adapter.grammar_id = "mock-full-parse"
    adapter.parser_mode = "full-parse"
    adapter.language_ids = ["mock"]
    adapter.trivia_node_types = []
    adapter.priority = 10
    adapter.detect_language.return_value = "mock"
    adapter.process.return_value = tree
    return adapter


def _make_differ_with_adapter(adapter: ParserAdapter) -> SemanticDiffer:
    """Return a SemanticDiffer whose registry always resolves to ``adapter``."""
    registry = MagicMock(spec=PluginRegistry)
    registry.detect_parser.return_value = (adapter, "mock")
    registry.get_renderer.side_effect = KeyError("no renderer")
    differ = SemanticDiffer(config=DiffConfig(), registry=registry)
    return differ


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullParseMode:
    def test_does_not_crash_on_raw_source(self):
        """
        Before the fix, _run_pipeline passed raw source text to
        _strip_trivia_impl which called json.loads() and raised JSONDecodeError.
        This test verifies that full-parse mode completes without error.
        """
        old_src = "def foo():\n    return 1\n"
        new_src = "def foo():\n    return 2\n"

        old_tree = _module_node("root_old", [_leaf_node("n1", "foo")])
        new_tree = _module_node("root_new", [_leaf_node("n2", "foo")])

        adapter = _make_full_parse_adapter(new_tree)
        # Return different trees for old vs new call
        adapter.process.side_effect = [old_tree, new_tree]

        differ = _make_differ_with_adapter(adapter)
        diff = differ.diff_strings(old_src, new_src, "test.mock")

        assert isinstance(diff, SemanticDiff)
        assert diff.language == "mock"

    def test_returns_semantic_changes_when_trees_differ(self):
        """Full-parse plugins that return different trees produce changes."""
        old_tree = _module_node("root_old", [_leaf_node("n1", "foo")])
        new_tree = _module_node("root_new", [_leaf_node("n2", "bar")])

        adapter = _make_full_parse_adapter(new_tree)
        adapter.process.side_effect = [old_tree, new_tree]

        differ = _make_differ_with_adapter(adapter)
        diff = differ.diff_strings("old source", "new source", "test.mock")

        assert diff.has_semantic_changes

    def test_returns_no_changes_for_identical_trees(self):
        """
        When a full-parse plugin returns structurally identical trees for both
        old and new, the diff should report no semantic changes.

        Note: the style-only structural-hash shortcut (stages 5-6) is skipped
        for full-parse mode, so we rely solely on the GumTree engine producing
        an empty edit script.
        """
        leaf = _leaf_node("n1", "foo")
        root = _module_node("root", [leaf])

        adapter = _make_full_parse_adapter(root)
        # Both calls return the same tree object — structurally identical
        adapter.process.side_effect = [root, root]

        differ = _make_differ_with_adapter(adapter)
        diff = differ.diff_strings("source", "source", "test.mock")

        assert not diff.has_semantic_changes

    def test_process_called_with_raw_source(self):
        """
        For full-parse mode the host must pass raw source to process(),
        NOT a CST JSON string.
        """
        old_src = "SELECT 1"
        new_src = "SELECT 2"

        leaf = _leaf_node("n1", "x")
        root = _module_node("root", [leaf])

        adapter = _make_full_parse_adapter(root)
        adapter.process.side_effect = [root, root]

        differ = _make_differ_with_adapter(adapter)
        differ.diff_strings(old_src, new_src, "query.mock")

        calls = adapter.process.call_args_list
        assert calls[0][0][0] == old_src, "old raw source must be passed to process()"
        assert calls[1][0][0] == new_src, "new raw source must be passed to process()"

    def test_cst_size_guard_not_applied_in_full_parse_mode(self):
        """
        The CST size guard (max_cst_bytes) applies to the serialised CST JSON
        for interpret-cst mode only. Full-parse mode must not be blocked by it
        even when the raw source exceeds max_cst_bytes.
        """
        # A large-ish source string
        large_src = "x = 1\n" * 1000
        config = DiffConfig(max_cst_bytes=100)  # tiny limit

        leaf = _leaf_node("n1", "x")
        root = _module_node("root", [leaf])

        adapter = _make_full_parse_adapter(root)
        adapter.process.side_effect = [root, root]

        differ = _make_differ_with_adapter(adapter)
        differ._config = config

        # Must not raise — size guard only fires for interpret-cst CST JSON
        diff = differ.diff_strings(large_src, large_src, "test.mock")
        assert isinstance(diff, SemanticDiff)
