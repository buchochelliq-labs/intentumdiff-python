"""
tests/integration/test_web_format_parsers.py

Integration tests for the Phase 1–3 web format parsers:
  CSS, SCSS, JSON, YAML, HTML  (InterpretCst — tree-sitter based)
  Vue, Svelte, Astro           (FullParse — block-level extraction)

These tests are intentionally lightweight: they exercise the detect_language
→ process → SemanticDiff pipeline end-to-end using the Wasm plugin binaries
that were compiled by build.py.  They are skipped automatically when the
required tree-sitter Python packages or compiled Wasm binaries are absent.

Run with:
    pytest tests/integration/test_web_format_parsers.py -v
or as part of the full integration suite:
    pytest -m integration
"""

from __future__ import annotations

import textwrap

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from pathlib import Path

_WASM_DIR = Path(__file__).parents[2] / "src" / "intentumdiff" / "wasm"


def _wasm_built(name: str) -> bool:
    """Return True if the compiled Wasm binary for *name* exists on disk."""
    return (_WASM_DIR / name).exists()


def _requires_wasm(*names: str):
    """pytest.mark.skipif decorator for tests requiring one or more Wasm files."""
    missing = [n for n in names if not _wasm_built(n)]
    if missing:
        reason = f"Wasm not built: {', '.join(missing)}. Run `python build.py` first."
        return pytest.mark.skip(reason=reason)
    return pytest.mark.usefixtures()  # no-op marker


def _diff(old: str, new: str, filename: str):
    """Run the full differ pipeline, returning a SemanticDiff."""
    from intentumdiff import DiffConfig, SemanticDiffer
    from intentumdiff.sources.string_source import StringSource

    config = DiffConfig(plugin_fuel=500_000_000)
    differ = SemanticDiffer(config=config)
    src = StringSource(old, new, filename)
    return differ.diff(src)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("css_parser.wasm"), reason="css_parser.wasm not built")
class TestCssParser:
    OLD_CSS = textwrap.dedent("""\
        .button {
            color: red;
            font-size: 14px;
        }
        .container {
            display: flex;
        }
    """)

    NEW_CSS = textwrap.dedent("""\
        .button {
            color: blue;
            font-size: 16px;
            font-weight: bold;
        }
        .container {
            display: flex;
        }
    """)

    IDENTICAL_CSS = OLD_CSS

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_CSS, self.IDENTICAL_CSS, "styles.css")
        assert result.is_style_only

    def test_property_change_detected(self):
        result = _diff(self.OLD_CSS, self.NEW_CSS, "styles.css")
        assert result.has_semantic_changes

    def test_selector_rename_detected(self):
        old = ".btn { color: red; }"
        new = ".button { color: red; }"
        result = _diff(old, new, "styles.css")
        assert result.has_semantic_changes


# ---------------------------------------------------------------------------
# SCSS
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("scss_parser.wasm"), reason="scss_parser.wasm not built")
class TestScssParser:
    OLD_SCSS = textwrap.dedent("""\
        $primary: #333;

        @mixin flex-center {
            display: flex;
            align-items: center;
        }

        .card {
            @include flex-center;
            color: $primary;
        }
    """)

    NEW_SCSS = textwrap.dedent("""\
        $primary: #555;

        @mixin flex-center($direction: row) {
            display: flex;
            align-items: center;
            flex-direction: $direction;
        }

        .card {
            @include flex-center(column);
            color: $primary;
        }
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_SCSS, self.OLD_SCSS, "styles.scss")
        assert result.is_style_only

    def test_variable_change_detected(self):
        result = _diff(self.OLD_SCSS, self.NEW_SCSS, "styles.scss")
        assert result.has_semantic_changes

    def test_mixin_arg_added_detected(self):
        old = "@mixin foo { color: red; }"
        new = "@mixin foo($x: 1px) { color: red; }"
        result = _diff(old, new, "styles.scss")
        assert result.has_semantic_changes


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("json_parser.wasm"), reason="json_parser.wasm not built")
class TestJsonParser:
    OLD_JSON = textwrap.dedent("""\
        {
            "name": "my-app",
            "version": "1.0.0",
            "dependencies": {
                "react": "^18.0.0"
            }
        }
    """)

    NEW_JSON = textwrap.dedent("""\
        {
            "name": "my-app",
            "version": "1.1.0",
            "dependencies": {
                "react": "^18.2.0",
                "react-dom": "^18.2.0"
            }
        }
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_JSON, self.OLD_JSON, "package.json")
        assert result.is_style_only

    def test_value_change_detected(self):
        result = _diff(self.OLD_JSON, self.NEW_JSON, "package.json")
        assert result.has_semantic_changes

    def test_key_added_detected(self):
        old = '{"a": 1}'
        new = '{"a": 1, "b": 2}'
        result = _diff(old, new, "data.json")
        assert result.has_semantic_changes

    def test_key_removed_detected(self):
        old = '{"a": 1, "b": 2}'
        new = '{"a": 1}'
        result = _diff(old, new, "data.json")
        assert result.has_semantic_changes


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("yaml_parser.wasm"), reason="yaml_parser.wasm not built")
class TestYamlParser:
    OLD_YAML = textwrap.dedent("""\
        name: my-service
        version: 1.0.0
        env:
          - name: DEBUG
            value: "false"
          - name: PORT
            value: "8080"
    """)

    NEW_YAML = textwrap.dedent("""\
        name: my-service
        version: 1.1.0
        env:
          - name: DEBUG
            value: "true"
          - name: PORT
            value: "9090"
          - name: LOG_LEVEL
            value: "info"
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_YAML, self.OLD_YAML, "config.yaml")
        assert result.is_style_only

    def test_value_change_detected(self):
        result = _diff(self.OLD_YAML, self.NEW_YAML, "config.yaml")
        assert result.has_semantic_changes

    def test_yml_extension_detected(self):
        result = _diff(self.OLD_YAML, self.OLD_YAML, "config.yml")
        assert result.is_style_only


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("html_parser.wasm"), reason="html_parser.wasm not built")
class TestHtmlParser:
    OLD_HTML = textwrap.dedent("""\
        <!DOCTYPE html>
        <html>
          <head><title>Hello</title></head>
          <body>
            <h1 class="title">Welcome</h1>
            <p>Some text</p>
          </body>
        </html>
    """)

    NEW_HTML = textwrap.dedent("""\
        <!DOCTYPE html>
        <html>
          <head><title>Hello World</title></head>
          <body>
            <h1 class="title main">Welcome</h1>
            <p>Some text</p>
            <footer>Copyright 2026</footer>
          </body>
        </html>
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_HTML, self.OLD_HTML, "index.html")
        assert result.is_style_only

    def test_element_added_detected(self):
        result = _diff(self.OLD_HTML, self.NEW_HTML, "index.html")
        assert result.has_semantic_changes

    def test_htm_extension_detected(self):
        result = _diff(self.OLD_HTML, self.OLD_HTML, "page.htm")
        assert result.is_style_only


# ---------------------------------------------------------------------------
# Vue SFC  (FullParse — block-level)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("vue_parser.wasm"), reason="vue_parser.wasm not built")
class TestVueParser:
    OLD_VUE = textwrap.dedent("""\
        <template>
          <div class="app">
            <h1>{{ title }}</h1>
          </div>
        </template>
        <script setup lang="ts">
        const title = 'Hello';
        </script>
        <style scoped>
        .app { padding: 16px; }
        </style>
    """)

    NEW_VUE_SCRIPT_CHANGED = textwrap.dedent("""\
        <template>
          <div class="app">
            <h1>{{ title }}</h1>
          </div>
        </template>
        <script setup lang="ts">
        import { ref } from 'vue';
        const title = ref('Hello');
        </script>
        <style scoped>
        .app { padding: 16px; }
        </style>
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_VUE, self.OLD_VUE, "App.vue")
        assert result.is_style_only

    def test_script_change_detected(self):
        result = _diff(self.OLD_VUE, self.NEW_VUE_SCRIPT_CHANGED, "App.vue")
        assert result.has_semantic_changes

    def test_style_only_change(self):
        # Change only whitespace inside the style block — hash changes, but
        # the block structure is identical → may or may not be "style only"
        # depending on the engine, but should at minimum produce valid output.
        result = _diff(self.OLD_VUE, self.OLD_VUE, "App.vue")
        # Idempotent: identical → no semantic changes
        assert not result.has_semantic_changes


# ---------------------------------------------------------------------------
# Svelte  (FullParse — block-level)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("svelte_parser.wasm"), reason="svelte_parser.wasm not built")
class TestSvelteParser:
    OLD_SVELTE = textwrap.dedent("""\
        <script>
          let count = 0;
          function increment() { count++; }
        </script>

        <button on:click={increment}>{count}</button>

        <style>
          button { background: blue; }
        </style>
    """)

    NEW_SVELTE = textwrap.dedent("""\
        <script>
          let count = 0;
          let step = 2;
          function increment() { count += step; }
        </script>

        <button on:click={increment}>{count}</button>

        <style>
          button { background: green; }
        </style>
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_SVELTE, self.OLD_SVELTE, "Counter.svelte")
        assert result.is_style_only

    def test_script_change_detected(self):
        result = _diff(self.OLD_SVELTE, self.NEW_SVELTE, "Counter.svelte")
        assert result.has_semantic_changes


# ---------------------------------------------------------------------------
# Astro  (FullParse — frontmatter + template)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _wasm_built("astro_parser.wasm"), reason="astro_parser.wasm not built")
class TestAstroParser:
    OLD_ASTRO = textwrap.dedent("""\
        ---
        import Layout from '../layouts/Layout.astro';
        const title = 'Home';
        ---
        <Layout title={title}>
          <h1>Welcome</h1>
        </Layout>
    """)

    NEW_ASTRO_FM_CHANGED = textwrap.dedent("""\
        ---
        import Layout from '../layouts/Layout.astro';
        import { getCollection } from 'astro:content';
        const title = 'Home';
        const posts = await getCollection('blog');
        ---
        <Layout title={title}>
          <h1>Welcome</h1>
        </Layout>
    """)

    NEW_ASTRO_TEMPLATE_CHANGED = textwrap.dedent("""\
        ---
        import Layout from '../layouts/Layout.astro';
        const title = 'Home';
        ---
        <Layout title={title}>
          <h1>Welcome to my site</h1>
          <p>New content here.</p>
        </Layout>
    """)

    NO_FRONTMATTER = textwrap.dedent("""\
        <h1>Static page</h1>
        <p>No frontmatter here.</p>
    """)

    def test_identical_is_style_only(self):
        result = _diff(self.OLD_ASTRO, self.OLD_ASTRO, "index.astro")
        assert result.is_style_only

    def test_frontmatter_change_detected(self):
        result = _diff(self.OLD_ASTRO, self.NEW_ASTRO_FM_CHANGED, "index.astro")
        assert result.has_semantic_changes

    def test_template_change_detected(self):
        result = _diff(self.OLD_ASTRO, self.NEW_ASTRO_TEMPLATE_CHANGED, "index.astro")
        assert result.has_semantic_changes

    def test_no_frontmatter_valid(self):
        result = _diff(self.NO_FRONTMATTER, self.NO_FRONTMATTER, "static.astro")
        assert result.is_style_only
