"""
intentumdiff.testing
~~~~~~~~~~~~~~~~~~~~~~~~~

``PluginTestHarness`` — unit-test helper for plugin authors.

Allows testing a ``.wasm`` plugin without going through the full pipeline::

    from intentumdiff.testing import PluginTestHarness

    harness = PluginTestHarness("path/to/my_plugin.wasm")

    # Parser tests
    assert harness.grammar_id == "my-lang"
    tree = harness.process(cst_json, "my-lang", "example.ml")
    assert tree.node_type == "module"

    # Renderer tests
    rendered = harness.render(diff_json)
    assert "function" in rendered

    # Security helpers
    harness.assert_no_filesystem_access()   # WAT-level check
    harness.assert_fuel_exhausts(fuel=100)  # confirm infinite-loop guard
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from intentumdiff.core.models import DiffConfig, LanguagePluginInfo, SemanticNode
from intentumdiff.plugins.adapter import ParserAdapter, RendererAdapter
from intentumdiff.plugins.exceptions import PluginFuelExhausted, PluginOutputError
from intentumdiff.plugins.loader import load_plugin


class PluginTestHarness:
    """
    Thin wrapper for testing a single ``.wasm`` plugin file in isolation.

    Attributes are lazy — the plugin is loaded on first access so the harness
    can be constructed cheaply in test parametrize lists.
    """

    def __init__(
        self,
        wasm_path: str | Path,
        config: DiffConfig | None = None,
        plugin_type: str = "parser",
    ) -> None:
        """
        Parameters
        ----------
        wasm_path:
            Path to the compiled ``.wasm`` plugin file.
        config:
            Optional ``DiffConfig`` — defaults are used otherwise.
        plugin_type:
            ``"parser"`` or ``"renderer"`` — determines which adapter is used.
        """
        self._wasm_path = Path(wasm_path)
        self._config = config or DiffConfig()
        self._plugin_type = plugin_type
        self._parser_adapter: ParserAdapter | None = None
        self._renderer_adapter: RendererAdapter | None = None
        self._loaded_plugin: Any | None = None  # shared LoadedPlugin instance

    # ── Lazy adapters ────────────────────────────────────────────────────────

    @property
    def _plugin(self) -> Any:
        """Return the shared LoadedPlugin, loading it on first access."""
        if self._loaded_plugin is None:
            self._loaded_plugin = load_plugin(self._wasm_path, self._config.plugin_fuel)
        return self._loaded_plugin

    @property
    def _parser(self) -> ParserAdapter:
        if self._parser_adapter is None:
            self._parser_adapter = ParserAdapter(self._plugin)
        return self._parser_adapter

    @property
    def _renderer(self) -> RendererAdapter:
        if self._renderer_adapter is None:
            self._renderer_adapter = RendererAdapter(self._plugin)
        return self._renderer_adapter

    # ── Parser helpers ───────────────────────────────────────────────────────

    @property
    def grammar_id(self) -> str:
        return self._parser.grammar_id

    @property
    def language_ids(self) -> list[str]:
        return self._parser.language_ids

    @property
    def language_info(self) -> list[LanguagePluginInfo]:
        return self._parser.language_info

    @property
    def trivia_node_types(self) -> list[str]:
        return self._parser.trivia_node_types

    def detect_language(self, filename: str, content: str = "") -> str:
        return self._parser.detect_language(filename, content)

    def process(self, input_: str, language: str, filename: str) -> SemanticNode:
        return self._parser.process(input_, language, filename)

    # ── Renderer helpers ─────────────────────────────────────────────────────

    @property
    def format_name(self) -> str:
        return self._renderer.format_name

    def render(self, diff_json: str) -> str:
        return self._renderer.render(diff_json)

    # ── Security assertion helpers ───────────────────────────────────────────

    def assert_rejects_malformed_output(self, input_json: str = "null") -> None:
        """
        Assert that a malformed plugin output raises ``PluginOutputError``.

        Useful when testing a plugin that is expected to validate its own
        input and return an ``{"error": "..."}`` object.
        """
        import pytest

        with pytest.raises(PluginOutputError):
            self._parser.process(input_json, "unknown", "test.txt")

    def assert_fuel_exhausts(self, fuel: int = 100) -> None:
        """
        Assert that the plugin exhausts its fuel when given a tiny budget.

        Use this to confirm that infinite-loop guard works for the plugin.
        """
        import pytest

        plugin = load_plugin(self._wasm_path, fuel)
        adapter = ParserAdapter(plugin)
        with pytest.raises(PluginFuelExhausted):
            adapter.process("{}", "unknown", "test.txt")

    def assert_no_filesystem_access(self) -> None:
        """
        Assert the plugin does not import any WASI filesystem functions.

        Reads the Wasm binary and looks for ``path_open`` / ``fd_read``
        in the import section.  This is a static check — it does not execute
        the plugin.
        """
        binary = self._wasm_path.read_bytes()
        forbidden = [b"path_open", b"fd_read", b"fd_write", b"fd_seek"]
        for sym in forbidden:
            if sym in binary:
                raise AssertionError(
                    f"Plugin {self._wasm_path.name} imports forbidden WASI symbol "
                    f"{sym!r} — sandbox would not be effective."
                )
