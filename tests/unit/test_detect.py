"""
tests/unit/test_detect.py
~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for the library-level language detection API.

All tests mock PluginRegistry so no Wasm plugins are required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from intentumdiff.core.models import DetectionResult
from intentumdiff.differ import SemanticDiffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_parser(
    grammar_id: str,
    language_ids: list,
    detects_as: str = "",
    priority: int = 100,
    plugin_id: str | None = None,
) -> MagicMock:
    """Return a minimal ParserAdapter mock.

    ``detects_as`` is what ``detect_language('', content)`` returns;
    empty string means the parser cannot handle this content.
    """
    p = MagicMock()
    p.grammar_id = grammar_id
    p.plugin_id = plugin_id or grammar_id
    type(p).language_ids = PropertyMock(return_value=language_ids)
    type(p).priority = PropertyMock(return_value=priority)
    p.detect_language.return_value = detects_as
    p.can_parse.return_value = bool(detects_as)
    return p


def _differ_with_parsers(*parsers) -> SemanticDiffer:
    """Return a SemanticDiffer whose registry is pre-populated with mocks."""
    from intentumdiff.plugins.registry import PluginRegistry as _PR

    registry = MagicMock(spec=_PR)
    registry._config = MagicMock()
    registry._config.allowed_plugins = None
    type(registry).parsers = PropertyMock(return_value=list(parsers))
    # Wire detect_by_content through the real implementation using fake parsers.
    registry.detect_by_content = (
        lambda content,
        candidates=None,
        preferred_plugins=None,
        plugin_id=None: _PR.detect_by_content(
            registry,
            content,
            candidates,
            preferred_plugins,
            plugin_id,
        )
    )
    registry.get_parser_by_id = (
        lambda plugin_id,
        language=None: _PR.get_parser_by_id(
            registry,
            plugin_id,
            language=language,
        )
    )
    differ = SemanticDiffer.__new__(SemanticDiffer)
    differ._registry = registry
    return differ


# ---------------------------------------------------------------------------
# ParserAdapter.can_parse
# ---------------------------------------------------------------------------


class TestCanParse:
    def test_returns_true_when_detect_language_non_empty(self):
        from intentumdiff.plugins.adapter import ParserAdapter

        plugin = MagicMock()
        plugin.call_detect_language.return_value = "python"
        adapter = ParserAdapter(plugin)
        assert adapter.can_parse("def foo(): pass") is True
        plugin.call_detect_language.assert_called_once_with("", "def foo(): pass")

    def test_returns_false_when_detect_language_empty(self):
        from intentumdiff.plugins.adapter import ParserAdapter

        plugin = MagicMock()
        plugin.call_detect_language.return_value = ""
        adapter = ParserAdapter(plugin)
        assert adapter.can_parse("SELECT 1") is False

    def test_content_is_truncated_to_4096(self):
        from intentumdiff.plugins.adapter import ParserAdapter

        plugin = MagicMock()
        plugin.call_detect_language.return_value = ""
        adapter = ParserAdapter(plugin)
        big = "x" * 8000
        adapter.can_parse(big)
        called_content = plugin.call_detect_language.call_args[0][1]
        assert len(called_content) == 4096

    def test_language_info_uses_plugin_export_with_host_fields(self):
        from intentumdiff.plugins.adapter import ParserAdapter

        plugin = MagicMock()
        plugin.trusted = True
        plugin.call_grammar_id.return_value = "python"
        plugin.call_priority.return_value = 7
        plugin.call_language_ids.return_value = ["python"]
        plugin.call_language_info.return_value = [
            {
                "language_id": "python",
                "language_name": "Python",
                "language_short_name": "Py",
                "monaco_language": "python",
                "default_filename": "example.py",
                "language_file_extensions": [".py"],
                "author": "Plugin Author",
                "plugin_version": "1.2.3",
                "last_updated": "2026-05-19",
            }
        ]
        adapter = ParserAdapter(plugin)
        adapter.plugin_id = "dist:python:python"
        adapter.provenance = "plugin 1.2.3"

        info = adapter.language_info[0]

        assert info.plugin_id == "dist:python:python"
        assert info.grammar_id == "python"
        assert info.priority == 7
        assert info.is_trusted is True
        assert info.language_name == "Python"
        assert info.default_filename == "example.py"

    def test_language_info_falls_back_when_export_missing(self):
        from intentumdiff.plugins.adapter import ParserAdapter

        plugin = MagicMock()
        plugin.trusted = False
        plugin.call_grammar_id.return_value = "python"
        plugin.call_priority.return_value = 0
        plugin.call_language_ids.return_value = ["python"]
        plugin.call_language_info.return_value = []
        adapter = ParserAdapter(plugin)
        adapter.plugin_id = "dist:python:python"

        info = adapter.language_info[0]

        assert info.language_id == "python"
        assert info.monaco_language == "python"
        assert info.default_filename == "code.py"


# ---------------------------------------------------------------------------
# PluginRegistry.detect_by_content
# ---------------------------------------------------------------------------


class TestDetectByContent:
    def test_returns_matching_parser_result(self):
        py = _fake_parser("python-parser", ["python", "py"], detects_as="python", priority=100)
        sql = _fake_parser("sql-parser", ["sql"], detects_as="", priority=90)
        differ = _differ_with_parsers(py, sql)
        results = differ.detect_all("def foo(): pass")
        assert len(results) == 1
        assert results[0].language == "python"
        assert results[0].grammar_id == "python-parser"
        assert results[0].confidence == 1.0

    def test_returns_multiple_matches_in_priority_order(self):
        a = _fake_parser("a", ["lang-a"], detects_as="lang-a", priority=200)
        b = _fake_parser("b", ["lang-b"], detects_as="lang-b", priority=100)
        differ = _differ_with_parsers(b, a)  # deliberately reversed
        results = differ.detect_all("some code")
        assert [r.language for r in results] == ["lang-a", "lang-b"]
        assert results[0].confidence > results[1].confidence

    def test_candidates_filters_parsers(self):
        py = _fake_parser("python-parser", ["python"], detects_as="python")
        ts = _fake_parser("ts-parser", ["typescript"], detects_as="typescript")
        differ = _differ_with_parsers(py, ts)
        results = differ.detect_all("code", candidates=["python"])
        langs = [r.language for r in results]
        assert "python" in langs
        assert "typescript" not in langs

    def test_empty_content_returns_empty(self):
        py = _fake_parser("python-parser", ["python"], detects_as="")
        differ = _differ_with_parsers(py)
        assert differ.detect_all("") == []

    def test_confidence_decreases_with_rank(self):
        a = _fake_parser("a", ["x"], detects_as="x", priority=200)
        b = _fake_parser("b", ["y"], detects_as="y", priority=100)
        differ = _differ_with_parsers(a, b)
        results = differ.detect_all("code")
        assert results[0].confidence == 1.0
        assert results[1].confidence == round(1.0 / 2, 3)

    def test_preferred_plugin_promotes_duplicate_language(self):
        high = _fake_parser("high", ["python"], detects_as="python", priority=200, plugin_id="high")
        low = _fake_parser("low", ["python"], detects_as="python", priority=100, plugin_id="low")
        differ = _differ_with_parsers(high, low)

        results = differ.detect_all("def f(): pass", preferred_plugins={"python": "low"})

        assert [r.grammar_id for r in results[:2]] == ["low", "high"]

    def test_explicit_plugin_id_limits_detection_to_that_parser(self):
        high = _fake_parser("high", ["python"], detects_as="python", priority=200, plugin_id="high")
        low = _fake_parser("low", ["python"], detects_as="python", priority=100, plugin_id="low")
        differ = _differ_with_parsers(high, low)

        results = differ.detect_all("def f(): pass", plugin_id="low")

        assert [r.grammar_id for r in results] == ["low"]
        high.detect_language.assert_not_called()

    def test_explicit_plugin_id_must_claim_content(self):
        from intentumdiff.plugins.exceptions import PluginNotFoundError

        parser = _fake_parser("python-parser", ["python"], detects_as="", plugin_id="py")
        differ = _differ_with_parsers(parser)

        with pytest.raises(PluginNotFoundError):
            differ.detect_all("not python", plugin_id="py")


# ---------------------------------------------------------------------------
# SemanticDiffer.detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_returns_first_result(self):
        py = _fake_parser("python-parser", ["python"], detects_as="python")
        differ = _differ_with_parsers(py)
        result = differ.detect_language("def foo(): pass")
        assert isinstance(result, DetectionResult)
        assert result.language == "python"

    def test_returns_none_when_no_parser_matches(self):
        py = _fake_parser("python-parser", ["python"], detects_as="")
        differ = _differ_with_parsers(py)
        assert differ.detect_language("????") is None

    def test_candidates_forwarded(self):
        py = _fake_parser("python-parser", ["python"], detects_as="python")
        sql = _fake_parser("sql-parser", ["sql"], detects_as="sql")
        differ = _differ_with_parsers(py, sql)
        result = differ.detect_language("SELECT 1", candidates=["sql"])
        assert result is not None
        assert result.language == "sql"


# ---------------------------------------------------------------------------
# DetectionResult model
# ---------------------------------------------------------------------------


class TestDetectionResult:
    def test_frozen(self):
        r = DetectionResult(language="python", grammar_id="python-parser")
        with pytest.raises(Exception):
            r.language = "other"  # type: ignore[misc]

    def test_default_confidence(self):
        r = DetectionResult(language="go", grammar_id="go-parser")
        assert r.confidence == 1.0

    def test_confidence_range_enforced(self):
        with pytest.raises(Exception):
            DetectionResult(language="x", grammar_id="y", confidence=1.5)
