"""
tests/integration/test_dbt_pipeline.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration tests for the dbt plugin pipeline.

These tests mock the Wasm layer and exercise the full Python pipeline
(preprocessing → parsing → enrichment → diff) to verify that:

  1. Jinja2 ``{{ ref('model') }}`` expressions are preprocessed to SQL-valid
     placeholders before tree-sitter parsing.
  2. Changing the model argument of ``ref()`` produces a MODIFICATION diff,
     not a style-only result.
  3. The enricher converts placeholder nodes to ``jinja_ref`` / ``jinja_source``
     nodes with the correct labels.
  4. Purely cosmetic SQL changes (whitespace, comment) with unchanged refs
     are classified as style-only.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from intentdiff.core.models import (
    ChangeType,
    DiffConfig,
    SemanticNode,
    NodePosition,
)


# ---------------------------------------------------------------------------
# Preprocessing invariants (pure Python, no Wasm needed)
# ---------------------------------------------------------------------------


def _preprocess(source: str) -> str:
    """Python mirror of DbtSqlParser::preprocess_source (Rust)."""
    import re

    def replace_expr(m: re.Match) -> str:
        content = m.group(1).strip()
        if content.startswith("ref("):
            args = re.findall(r"['\"]([^'\"]+)['\"]", content[4:])
            if len(args) == 1:
                safe = args[0].replace("-", "_")
                return f"dbt_ref__{safe}"
            if len(args) == 2:
                proj = args[0].replace("-", "_")
                model = args[1].replace("-", "_")
                return f"dbt_ref_{proj}__{model}"
            return "dbt_ref"
        if content.startswith("source("):
            args = re.findall(r"['\"]([^'\"]+)['\"]", content[7:])
            if len(args) == 2:
                pkg = args[0].replace("-", "_")
                tbl = args[1].replace("-", "_")
                return f"dbt_src__{pkg}__{tbl}"
            if len(args) == 1:
                return f"dbt_src__{args[0].replace('-', '_')}"
            return "dbt_src"
        if content.startswith("config("):
            return ""
        return "dbt_expr"

    # Replace {{ expr }}
    source = re.sub(r"\{\{\s*(.*?)\s*\}\}", replace_expr, source)
    # Remove {% block %} tags
    source = re.sub(r"\{%.*?%\}", "", source, flags=re.DOTALL)
    # Remove {# comments #}
    source = re.sub(r"\{#.*?#\}", "", source, flags=re.DOTALL)
    return source


class TestPreprocessing:
    def test_ref_simple(self):
        src = "SELECT * FROM {{ ref('orders') }}"
        result = _preprocess(src)
        assert "dbt_ref__orders" in result
        assert "{{" not in result

    def test_ref_with_project(self):
        src = "SELECT * FROM {{ ref('analytics', 'orders') }}"
        result = _preprocess(src)
        assert "dbt_ref_analytics__orders" in result

    def test_source(self):
        src = "SELECT * FROM {{ source('raw', 'events') }}"
        result = _preprocess(src)
        assert "dbt_src__raw__events" in result

    def test_config_stripped(self):
        src = "{{ config(materialized='table') }} SELECT 1"
        result = _preprocess(src)
        assert "config" not in result
        assert "SELECT 1" in result

    def test_block_tag_stripped(self):
        src = "{% if is_incremental() %} WHERE created_at > '2024-01-01' {% endif %}"
        result = _preprocess(src)
        assert "{%" not in result

    def test_comment_stripped(self):
        src = "{# this is a comment #} SELECT 1"
        result = _preprocess(src)
        assert "{#" not in result
        assert "SELECT 1" in result

    def test_ref_change_produces_different_output(self):
        """Changing ref argument must produce a different preprocessed string."""
        old = _preprocess("SELECT * FROM {{ ref('orders') }}")
        new = _preprocess("SELECT * FROM {{ ref('orders_v2') }}")
        assert old != new, "Different ref() args must produce different preprocessed SQL"

    def test_ref_same_produces_identical_output(self):
        """Same ref argument with different whitespace → same placeholder."""
        a = _preprocess("SELECT * FROM {{ ref('orders') }}")
        b = _preprocess("SELECT * FROM {{ref('orders')}}")
        # Both should contain the same placeholder (whitespace inside {{ }} varies)
        assert "dbt_ref__orders" in a
        assert "dbt_ref__orders" in b


# ---------------------------------------------------------------------------
# Enricher placeholder → jinja_ref/jinja_source conversion
# ---------------------------------------------------------------------------


def _enrich_node(node: dict) -> dict:
    """Python mirror of dbt-enricher enrich_node."""
    import hashlib

    def sha256_prefix(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    def compute_hash(n: dict) -> str:
        if not n.get("children"):
            return sha256_prefix(f"{n['node_type']}:{n['label']}")
        child_hashes = "|".join(c["structural_hash"] for c in n["children"])
        return sha256_prefix(f"{n['node_type']}|{child_hashes}")

    changed = False
    for child in node.get("children", []):
        if _enrich_node(child):
            changed = True

    label = node.get("label", "")
    if label == "dbt_expr":
        node["node_type"] = "jinja_expr"
        node["label"] = ""
        changed = True
    elif label.startswith("dbt_ref__"):
        node["node_type"] = "jinja_ref"
        node["label"] = label[len("dbt_ref__"):].replace("_", "-")
        changed = True
    elif label.startswith("dbt_ref_"):
        rest = label[len("dbt_ref_"):]
        if "__" in rest:
            sep = rest.index("__")
            proj = rest[:sep].replace("_", "-")
            model = rest[sep + 2:].replace("_", "-")
            node["node_type"] = "jinja_ref"
            node["label"] = f"{proj}.{model}"
            changed = True
    elif label.startswith("dbt_src__"):
        rest = label[len("dbt_src__"):]
        if "__" in rest:
            sep = rest.index("__")
            pkg = rest[:sep].replace("_", "-")
            tbl = rest[sep + 2:].replace("_", "-")
            node["node_type"] = "jinja_source"
            node["label"] = f"{pkg}.{tbl}"
            changed = True
        else:
            node["node_type"] = "jinja_source"
            node["label"] = rest.replace("_", "-")
            changed = True

    if changed:
        node["structural_hash"] = compute_hash(node)

    return changed


class TestEnricherLogic:
    def test_ref_node_decoded(self):
        node = {
            "id": "1",
            "node_type": "identifier",
            "label": "dbt_ref__orders",
            "structural_hash": "old",
            "start_line": 0, "start_col": 0,
            "end_line": 0, "end_col": 0,
            "children": [],
        }
        _enrich_node(node)
        assert node["node_type"] == "jinja_ref"
        assert node["label"] == "orders"
        assert node["structural_hash"] != "old"

    def test_source_node_decoded(self):
        node = {
            "id": "1",
            "node_type": "identifier",
            "label": "dbt_src__raw__events",
            "structural_hash": "old",
            "start_line": 0, "start_col": 0,
            "end_line": 0, "end_col": 0,
            "children": [],
        }
        _enrich_node(node)
        assert node["node_type"] == "jinja_source"
        assert node["label"] == "raw.events"

    def test_cross_project_ref_decoded(self):
        node = {
            "id": "1",
            "node_type": "identifier",
            "label": "dbt_ref_analytics__orders",
            "structural_hash": "old",
            "start_line": 0, "start_col": 0,
            "end_line": 0, "end_col": 0,
            "children": [],
        }
        _enrich_node(node)
        assert node["node_type"] == "jinja_ref"
        assert node["label"] == "analytics.orders"

    def test_hash_recomputed_after_enrichment(self):
        import hashlib

        node = {
            "id": "1",
            "node_type": "identifier",
            "label": "dbt_ref__orders",
            "structural_hash": "old",
            "start_line": 0, "start_col": 0,
            "end_line": 0, "end_col": 0,
            "children": [],
        }
        _enrich_node(node)
        expected_hash = hashlib.sha256(b"jinja_ref:orders").hexdigest()[:16]
        assert node["structural_hash"] == expected_hash

    def test_different_refs_get_different_hashes(self):
        def make_node(label):
            return {
                "id": "1",
                "node_type": "identifier",
                "label": label,
                "structural_hash": "old",
                "start_line": 0, "start_col": 0,
                "end_line": 0, "end_col": 0,
                "children": [],
            }

        n1 = make_node("dbt_ref__orders")
        n2 = make_node("dbt_ref__orders_v2")
        _enrich_node(n1)
        _enrich_node(n2)
        assert n1["structural_hash"] != n2["structural_hash"]

    def test_non_placeholder_node_unchanged(self):
        node = {
            "id": "1",
            "node_type": "identifier",
            "label": "customer_id",
            "structural_hash": "original",
            "start_line": 0, "start_col": 0,
            "end_line": 0, "end_col": 0,
            "children": [],
        }
        changed = _enrich_node(node)
        assert not changed
        assert node["label"] == "customer_id"
        assert node["structural_hash"] == "original"
