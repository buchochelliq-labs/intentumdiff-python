"""
tests/unit/test_diff_analyzer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for the DiffAnalyzerAdapter framework and custom ChangeType strings.

No Wasm plugin binary is required — adapters are exercised via lightweight
Python fakes that mirror the Wasm adapter protocol.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from intentumdiff.core.models import (
    Change,
    ChangeType,
    DiffConfig,
    NodePosition,
    SemanticDiff,
    SemanticNode,
)
from intentumdiff.plugins.adapter import DiffAnalyzerAdapter
from intentumdiff.plugins.registry import PluginRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_POS = NodePosition(start_line=0, start_col=0, end_line=0, end_col=1)


def _make_node(id_: str, label: str = "x") -> SemanticNode:
    return SemanticNode(
        id=id_,
        node_type="identifier",
        label=label,
        position=_POS,
        structural_hash="deadbeef",
        children=[],
    )


def _simple_diff(**kwargs) -> SemanticDiff:
    defaults = dict(
        old_filename="test.py",
        new_filename="test.py",
        language="python",
        changes=[],
        style_only=False,
        old_hash="aaa",
        new_hash="bbb",
    )
    defaults.update(kwargs)
    return SemanticDiff(**defaults)


# ---------------------------------------------------------------------------
# Change.change_type accepts custom strings (ChangeType | str)
# ---------------------------------------------------------------------------


class TestChangeTypeUnion:
    def test_builtin_change_type_accepted(self):
        c = Change(change_type=ChangeType.MODIFICATION, new_node=_make_node("n1"))
        assert c.change_type == ChangeType.MODIFICATION
        assert isinstance(c.change_type, ChangeType)

    def test_custom_string_accepted(self):
        c = Change(
            change_type="SQL_RENAME_TABLE",
            old_node=_make_node("o1"),
            new_node=_make_node("n1"),
        )
        assert c.change_type == "SQL_RENAME_TABLE"

    def test_known_string_coerces_to_enum(self):
        c = Change(change_type="ADDITION", new_node=_make_node("n1"))
        assert c.change_type == ChangeType.ADDITION

    def test_unknown_string_stays_as_str(self):
        c = Change(
            change_type="GO_INTERFACE_CHANGE",
            new_node=_make_node("n1"),
        )
        assert isinstance(c.change_type, str)
        assert not isinstance(c.change_type, ChangeType)

    def test_existing_validator_still_fires_for_addition(self):
        with pytest.raises(Exception, match="old_node"):
            Change(
                change_type=ChangeType.ADDITION,
                old_node=_make_node("o1"),
                new_node=_make_node("n1"),
            )

    def test_custom_string_bypasses_builtin_validator(self):
        # Custom change types are NOT constrained by the built-in node rules
        c = Change(
            change_type="CUSTOM_OP",
            old_node=_make_node("o1"),
            new_node=_make_node("n1"),
        )
        assert c.change_type == "CUSTOM_OP"

    def test_serializes_custom_type_to_json(self):
        c = Change(change_type="MY_CUSTOM_TYPE", new_node=_make_node("n1"))
        data = json.loads(c.model_dump_json())
        assert data["change_type"] == "MY_CUSTOM_TYPE"

    def test_roundtrip_custom_type(self):
        c = Change(change_type="MY_CUSTOM_TYPE", new_node=_make_node("n1"))
        restored = Change.model_validate_json(c.model_dump_json())
        assert restored.change_type == "MY_CUSTOM_TYPE"


# ---------------------------------------------------------------------------
# DiffAnalyzerAdapter protocol
# ---------------------------------------------------------------------------


class _FakePlugin:
    """Minimal stand-in for LoadedPlugin to test DiffAnalyzerAdapter without Wasm."""

    def __init__(self, lang_ids=("python",), priority_val=0, transform=None):
        self._lang_ids = list(lang_ids)
        self._priority_val = priority_val
        self._transform = transform or (lambda d, l, f: d)

    def call_language_ids(self):
        return self._lang_ids

    def call_priority(self):
        return self._priority_val

    def call_analyze_diff(self, diff_json, language, filename, fuel=None):
        return self._transform(diff_json, language, filename)

    def drain_telemetry(self):
        # Real LoadedPlugins expose drain_telemetry; the adapter calls it after every
        # analyze pass. Mirror it here or the pipeline logs a failure and skips the analyzer.
        return []


def _make_adapter(lang_ids=("python",), priority=0, transform=None):
    adapter = DiffAnalyzerAdapter(_FakePlugin(lang_ids, priority, transform))
    adapter.provenance = "fake-plugin 0.0.1"
    return adapter


class TestDiffAnalyzerAdapter:
    def test_language_ids_cached(self):
        adapter = _make_adapter(lang_ids=["sql"])
        _ = adapter.language_ids
        _ = adapter.language_ids  # second access should hit cache
        assert adapter.language_ids == ["sql"]

    def test_priority(self):
        adapter = _make_adapter(priority=42)
        assert adapter.priority == 42

    def test_analyze_diff_passthrough(self):
        diff = _simple_diff()
        adapter = _make_adapter()
        result = adapter.analyze_diff(diff.model_dump_json(), "python", "test.py")
        # passthrough — returns unchanged JSON
        restored = SemanticDiff.model_validate_json(result)
        assert restored == diff

    def test_analyze_diff_injects_custom_change(self):
        def _add_custom(diff_json, language, filename):
            data = json.loads(diff_json)
            data["changes"].append(
                {
                    "change_type": "PYTHON_DECORATOR_CHANGE",
                    "old_node": None,
                    "new_node": {
                        "id": "n99",
                        "node_type": "decorator",
                        "label": "@cached",
                        "position": {"start_line": 0, "start_col": 0, "end_line": 0, "end_col": 1},
                        "structural_hash": "aabbcc",
                        "children": [],
                    },
                    "refactoring_kind": None,
                    "confidence": 0.9,
                    "description": "Decorator added",
                }
            )
            return json.dumps(data)

        node = _make_node("n1")
        diff = _simple_diff(
            changes=[Change(change_type=ChangeType.MODIFICATION, new_node=node)]
        )
        adapter = _make_adapter(transform=_add_custom)
        result_json = adapter.analyze_diff(diff.model_dump_json(), "python", "test.py")
        result = SemanticDiff.model_validate_json(result_json)

        assert len(result.changes) == 2
        custom = result.changes[1]
        assert custom.change_type == "PYTHON_DECORATOR_CHANGE"
        assert isinstance(custom.change_type, str)
        assert not isinstance(custom.change_type, ChangeType)


# ---------------------------------------------------------------------------
# Registry: get_diff_analyzers filters by language
# ---------------------------------------------------------------------------


class TestRegistryDiffAnalyzers:
    def test_no_analyzers_registered_returns_empty(self):
        registry = PluginRegistry()
        # No external plugins installed in test env — should return []
        result = registry.get_diff_analyzers("python")
        assert result == []

    def test_get_diff_analyzers_filters_by_language(self):
        registry = PluginRegistry()

        sql_adapter = _make_adapter(lang_ids=["sql"])
        py_adapter = _make_adapter(lang_ids=["python"])

        registry._diff_analyzers = [sql_adapter, py_adapter]

        assert registry.get_diff_analyzers("python") == [py_adapter]
        assert registry.get_diff_analyzers("sql") == [sql_adapter]
        assert registry.get_diff_analyzers("go") == []

    def test_get_diff_analyzers_sorted_by_priority(self):
        registry = PluginRegistry()

        low = _make_adapter(lang_ids=["python"], priority=1)
        high = _make_adapter(lang_ids=["python"], priority=10)
        mid = _make_adapter(lang_ids=["python"], priority=5)

        registry._diff_analyzers = [low, high, mid]

        result = registry.get_diff_analyzers("python")
        assert result == [high, mid, low]


# ---------------------------------------------------------------------------
# Pipeline integration: stage 13.5 invoked
# ---------------------------------------------------------------------------


class TestPipelineDiffAnalyzerStage:
    """
    These tests exercise the real pipeline (stages 1–13.5).
    They require the tree-sitter extras and Wasm plugins; they are skipped
    when those are not available.
    """

    def test_stage_13_5_updates_diff(self):
        """Stage 13.5 is called and can inject a custom change_type."""
        pytest.importorskip("tree_sitter_javascript")

        from intentumdiff import SemanticDiffer
        from intentumdiff.sources.string_source import StringSource

        def _inject(diff_json, language, filename):
            data = json.loads(diff_json)
            if data["changes"]:
                data["changes"][0]["change_type"] = "CUSTOM_OVERRIDE"
            return json.dumps(data)

        registry = PluginRegistry()
        adapter = _make_adapter(lang_ids=["sql"], transform=_inject)
        registry._diff_analyzers = [adapter]

        differ = SemanticDiffer(registry=registry)
        # This test exercises stage-13.5 injection; it just needs an input that reliably
        # produces a change to override. (The old caveat about SELECT 1 vs SELECT 2 hashing
        # equal was fixed in issue #16 — numeric literals now diff as modifications.)
        src = StringSource("SELECT a;\n", "SELECT b;\n", "test.sql")
        diff = differ.diff(src)

        custom_types = [
            c.change_type for c in diff.changes if c.change_type == "CUSTOM_OVERRIDE"
        ]
        assert len(custom_types) > 0

    def test_stage_13_5_skips_failing_analyzer(self):
        """A crashing analyzer is skipped; the diff is still returned."""
        pytest.importorskip("tree_sitter_javascript")

        from intentumdiff import SemanticDiffer
        from intentumdiff.sources.string_source import StringSource

        def _raise(diff_json, language, filename):
            raise RuntimeError("analyzer exploded")

        registry = PluginRegistry()
        adapter = _make_adapter(lang_ids=["sql"], transform=_raise)
        registry._diff_analyzers = [adapter]

        differ = SemanticDiffer(registry=registry)
        src = StringSource("SELECT 1;\n", "SELECT 2;\n", "test.sql")
        diff = differ.diff(src)  # must not raise

        for c in diff.changes:
            assert isinstance(c.change_type, (ChangeType, str))
