"""
tests/unit/test_dbt_yaml.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for the dbt schema/YAML parser logic.

These tests exercise the Python-level behaviour that mirrors the Wasm plugin:
  - Language detection heuristics
  - Schema YAML structure expectations

No Wasm binary is loaded; the tests validate invariants that both the Python
integration and the Rust plugin must satisfy.
"""

from __future__ import annotations

import json

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml is required for dbt YAML tests")


# ---------------------------------------------------------------------------
# Language detection helpers (mirror of Rust detect() function)
# ---------------------------------------------------------------------------


def detect_language(filename: str, content: str) -> str:
    """Python mirror of DbtSchemaParser::detect_language."""
    f = filename.lower()
    basename = f.replace("\\", "/").rsplit("/", 1)[-1]

    if basename in ("dbt_project.yml", "dbt_project.yaml"):
        return "dbt-config"
    if basename in ("packages.yml", "packages.yaml"):
        return "dbt-packages"
    if f.endswith((".jinja", ".jinja2", ".j2")):
        return "dbt-jinja"
    if f.endswith((".yml", ".yaml")):
        schema_markers = ("version: 2", "models:", "sources:", "exposures:", "seeds:", "snapshots:")
        if any(m in content for m in schema_markers):
            return "dbt-yaml"
    return ""


# ---------------------------------------------------------------------------
# detect_language tests
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    @pytest.mark.parametrize(
        "filename, content, expected",
        [
            ("dbt_project.yml", "name: myproject", "dbt-config"),
            ("dbt_project.yaml", "name: myproject", "dbt-config"),
            ("packages.yml", "packages:", "dbt-packages"),
            ("macros/utils.jinja", "", "dbt-jinja"),
            ("macros/utils.jinja2", "", "dbt-jinja"),
            ("models/schema.yml", "version: 2\nmodels:\n  - name: orders", "dbt-yaml"),
            ("models/sources.yml", "sources:\n  - name: raw", "dbt-yaml"),
            ("models/exposures.yml", "exposures:\n  - name: e", "dbt-yaml"),
            ("models/orders.sql", "SELECT 1", ""),
            ("plain.yml", "key: value", ""),
        ],
    )
    def test_detect(self, filename, content, expected):
        assert detect_language(filename, content) == expected


# ---------------------------------------------------------------------------
# Schema YAML structure tests
# ---------------------------------------------------------------------------


SCHEMA_YAML = """\
version: 2

models:
  - name: orders
    description: All orders
    columns:
      - name: id
        description: Primary key
        tests:
          - unique
          - not_null
      - name: status
        tests:
          - accepted_values:
              values: [placed, completed, cancelled]

sources:
  - name: raw
    tables:
      - name: raw_orders
        columns:
          - name: id

exposures:
  - name: orders_dashboard
    type: dashboard
"""


class TestSchemaYamlStructure:
    """Test invariants about what a parsed dbt schema YAML must contain."""

    def test_has_models_key(self):
        data = yaml.safe_load(SCHEMA_YAML)
        assert "models" in data
        assert isinstance(data["models"], list)

    def test_model_has_name(self):
        data = yaml.safe_load(SCHEMA_YAML)
        model = data["models"][0]
        assert model["name"] == "orders"

    def test_model_has_columns(self):
        data = yaml.safe_load(SCHEMA_YAML)
        model = data["models"][0]
        col_names = [c["name"] for c in model.get("columns", [])]
        assert "id" in col_names
        assert "status" in col_names

    def test_column_tests_captured(self):
        data = yaml.safe_load(SCHEMA_YAML)
        model = data["models"][0]
        id_col = next(c for c in model["columns"] if c["name"] == "id")
        tests = id_col.get("tests", [])
        # tests can be str or dict
        test_names = [t if isinstance(t, str) else list(t.keys())[0] for t in tests]
        assert "unique" in test_names
        assert "not_null" in test_names

    def test_has_sources(self):
        data = yaml.safe_load(SCHEMA_YAML)
        assert "sources" in data
        src = data["sources"][0]
        assert src["name"] == "raw"
        table_names = [t["name"] for t in src.get("tables", [])]
        assert "raw_orders" in table_names

    def test_has_exposures(self):
        data = yaml.safe_load(SCHEMA_YAML)
        assert "exposures" in data
        assert data["exposures"][0]["name"] == "orders_dashboard"
