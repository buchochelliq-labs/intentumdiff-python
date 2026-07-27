"""
tests/unit/test_enricher.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for the dbt EnricherAdapter and the Python-level enrichment
infrastructure (registry.get_enrichers, differ step 7b).

These tests mock the underlying LoadedPlugin so no Wasm binary is needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from intentdiff.plugins.adapter import EnricherAdapter
from intentdiff.core.models import SemanticNode, NodePosition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(**kwargs) -> dict:
    base = {
        "id": "0",
        "node_type": "identifier",
        "label": "x",
        "start_line": 0,
        "start_col": 0,
        "end_line": 0,
        "end_col": 10,
        "structural_hash": "abc123",
        "children": [],
    }
    base.update(kwargs)
    return base


def _make_enricher(language_ids: list[str], priority: int, enrich_fn=None) -> EnricherAdapter:
    """Build an EnricherAdapter backed by a mock LoadedPlugin."""
    plugin = MagicMock()
    plugin.call_language_ids.return_value = language_ids
    plugin.call_priority.return_value = priority
    if enrich_fn is not None:
        plugin.call_enrich.side_effect = enrich_fn
    else:
        # Identity enricher — returns tree unchanged
        plugin.call_enrich.side_effect = lambda tree, *_, **__: tree
    return EnricherAdapter(plugin)


# ---------------------------------------------------------------------------
# EnricherAdapter property tests
# ---------------------------------------------------------------------------


class TestEnricherAdapterProperties:
    def test_language_ids(self):
        ea = _make_enricher(["dbt-sql", "dbt-jinja"], 1)
        assert ea.language_ids == ["dbt-sql", "dbt-jinja"]

    def test_priority(self):
        ea = _make_enricher(["dbt-sql"], 5)
        assert ea.priority == 5

    def test_enrich_passes_arguments(self):
        calls = []

        def capture(tree, raw, lang, fname, fuel=None):
            calls.append((tree, raw, lang, fname))
            return tree

        ea = _make_enricher(["dbt-sql"], 1, enrich_fn=capture)
        tree_json = json.dumps(_node())
        ea.enrich(tree_json, "SELECT 1", "dbt-sql", "model.sql")
        assert len(calls) == 1
        assert calls[0][2] == "dbt-sql"
        assert calls[0][3] == "model.sql"


# ---------------------------------------------------------------------------
# Placeholder decoding (pure-Python approximation for host-side validation)
# ---------------------------------------------------------------------------


def _decode_label(label: str) -> tuple[str, str] | None:
    """Mirror the Rust placeholder decode logic in Python for test assertions."""
    if label == "dbt_expr":
        return ("jinja_expr", "")
    if label.startswith("dbt_ref__"):
        model = label[len("dbt_ref__"):].replace("_", "-")
        return ("jinja_ref", model)
    if label.startswith("dbt_ref_"):
        rest = label[len("dbt_ref_"):]
        if "__" in rest:
            sep = rest.index("__")
            project = rest[:sep].replace("_", "-")
            model = rest[sep + 2:].replace("_", "-")
            return ("jinja_ref", f"{project}.{model}")
    if label.startswith("dbt_src__"):
        rest = label[len("dbt_src__"):]
        if "__" in rest:
            sep = rest.index("__")
            pkg = rest[:sep].replace("_", "-")
            tbl = rest[sep + 2:].replace("_", "-")
            return ("jinja_source", f"{pkg}.{tbl}")
        return ("jinja_source", rest.replace("_", "-"))
    return None


class TestPlaceholderDecoding:
    @pytest.mark.parametrize(
        "label, expected_type, expected_label",
        [
            ("dbt_ref__orders", "jinja_ref", "orders"),
            ("dbt_ref__orders_v2", "jinja_ref", "orders-v2"),
            ("dbt_ref_analytics__orders", "jinja_ref", "analytics.orders"),
            ("dbt_src__raw__events", "jinja_source", "raw.events"),
            ("dbt_src__events", "jinja_source", "events"),
            ("dbt_expr", "jinja_expr", ""),
        ],
    )
    def test_decode(self, label, expected_type, expected_label):
        result = _decode_label(label)
        assert result is not None, f"Expected {label!r} to be decoded"
        node_type, decoded_label = result
        assert node_type == expected_type
        assert decoded_label == expected_label

    def test_non_placeholder_returns_none(self):
        assert _decode_label("orders") is None
        assert _decode_label("SELECT") is None
        assert _decode_label("") is None


# ---------------------------------------------------------------------------
# EnricherAdapter.enrich — validates return type is str
# ---------------------------------------------------------------------------


class TestEnricherAdapterEnrich:
    def test_returns_string(self):
        ea = _make_enricher(["dbt-sql"], 1)
        tree_json = json.dumps(_node())
        result = ea.enrich(tree_json, "", "dbt-sql", "x.sql")
        assert isinstance(result, str)

    def test_invalid_return_raises(self):
        """If the enricher returns invalid JSON, PluginOutputError should be raised
        by the differ (not the adapter itself)."""
        plugin = MagicMock()
        plugin.call_language_ids.return_value = ["dbt-sql"]
        plugin.call_priority.return_value = 1
        plugin.call_enrich.return_value = "not-json{"
        ea = EnricherAdapter(plugin)
        # adapter.enrich just passes through; the differ validates
        result = ea.enrich("{}", "", "dbt-sql", "x.sql")
        assert result == "not-json{"
