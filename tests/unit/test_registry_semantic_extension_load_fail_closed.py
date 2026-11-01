"""Registered semantic extensions are required, not best-effort optional passes.

A broken installed analyzer or enricher must not quietly reduce the semantic
evidence used by a source-judged review (Python #236, #237).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from intentumdiff.plugins import registry as plugin_registry
from intentumdiff.plugins.exceptions import PluginLoadError


@pytest.mark.parametrize(
    ("loader_name", "group"),
    [
        ("_load_diff_analyzers", "intentumdiff.diff_analyzers"),
        ("_load_enrichers", "intentumdiff.enrichers"),
    ],
)
def test_no_registered_semantic_extensions_is_valid(monkeypatch, loader_name, group):
    """An unconfigured extension is different from a broken installed one."""
    seen = []

    def entry_points(*, group):
        seen.append(group)
        return []

    monkeypatch.setattr(plugin_registry.importlib.metadata, "entry_points", entry_points)
    assert getattr(plugin_registry, loader_name)() == []
    assert seen == [group]


@pytest.mark.parametrize(
    ("loader_name", "group", "adapter_name"),
    [
        ("_load_diff_analyzers", "intentumdiff.diff_analyzers", "DiffAnalyzerAdapter"),
        ("_load_enrichers", "intentumdiff.enrichers", "EnricherAdapter"),
    ],
)
@pytest.mark.parametrize("loaded_one_first", [False, True])
def test_installed_semantic_extension_load_error_never_returns_partial_success(
    monkeypatch, loader_name, group, adapter_name, loaded_one_first
):
    """Even a failure *after* a good adapter must abort discovery entirely."""
    entries = [SimpleNamespace(name="good")] if loaded_one_first else []
    entries.append(SimpleNamespace(name="broken"))
    seen_groups = []

    def entry_points(*, group):
        seen_groups.append(group)
        return entries

    def load_plugin(path, fuel, *, trusted):
        if path == "/good.wasm":
            return object()
        raise RuntimeError("broken Wasm component")

    monkeypatch.setattr(plugin_registry.importlib.metadata, "entry_points", entry_points)
    monkeypatch.setattr(
        plugin_registry, "_wasm_path_from_ep", lambda ep: f"/{ep.name}.wasm"
    )
    monkeypatch.setattr(plugin_registry, "_is_trusted_entry_point", lambda ep: True)
    monkeypatch.setattr(plugin_registry, "_provenance", lambda ep: "test")
    monkeypatch.setattr(plugin_registry, "load_plugin", load_plugin)
    monkeypatch.setattr(
        plugin_registry, adapter_name, lambda plugin: SimpleNamespace()
    )

    with pytest.raises(PluginLoadError, match="broken") as err:
        getattr(plugin_registry, loader_name)()

    assert group in str(err.value)
    assert "broken Wasm component" in str(err.value)
    assert isinstance(err.value.__cause__, RuntimeError)
    assert seen_groups == [group]


@pytest.mark.parametrize(
    ("loader_name", "group"),
    [
        ("_load_diff_analyzers", "intentumdiff.diff_analyzers"),
        ("_load_enrichers", "intentumdiff.enrichers"),
    ],
)
def test_registered_semantic_extension_metadata_error_is_fatal(
    monkeypatch, loader_name, group
):
    """Malformed entry-point metadata must not look like no configuration."""
    ep = SimpleNamespace(name="bad-metadata")
    monkeypatch.setattr(
        plugin_registry.importlib.metadata, "entry_points",
        lambda *, group: [ep],
    )

    def fail_resolve(_ep):
        raise ValueError("invalid entry point")

    monkeypatch.setattr(plugin_registry, "_wasm_path_from_ep", fail_resolve)

    with pytest.raises(PluginLoadError, match="bad-metadata") as err:
        getattr(plugin_registry, loader_name)()

    assert group in str(err.value)
    assert isinstance(err.value.__cause__, ValueError)
