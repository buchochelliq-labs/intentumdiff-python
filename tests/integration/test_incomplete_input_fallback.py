"""
tests/integration/test_incomplete_input_fallback.py
Incomplete input mid-edit (core #314, yaml-parser#9): when a parser component cannot read
one side and returns its in-band error envelope (half-typed YAML/XML reads
``Empty semantic tree``), the differ must return Rust's explicit source fallback with the
edit preserved, not raise. ``intentumdiff diff`` used to exit 1 with
``Plugin 'yaml' returned invalid output: Empty semantic tree``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_WASM_DIR = Path(__file__).parents[2] / "src" / "intentumdiff" / "wasm"

CASES = [
    pytest.param("config.yaml", "yaml_parser.wasm", "a:\n  b: 1\n", "a:\n  b: [1, \n", id="yaml"),
    pytest.param(
        "data.xml", "xml_parser.wasm", "<a>\n  <b>1</b>\n</a>\n", "<a>\n  <b>1</b\n", id="xml"
    ),
]


def _diff(old: str, new: str, filename: str, *, fallback: bool = True):
    from intentumdiff import DiffConfig, SemanticDiffer
    from intentumdiff.sources.string_source import StringSource

    config = DiffConfig(plugin_fuel=500_000_000, fallback_to_token_diff=fallback)
    return SemanticDiffer(config=config).diff(StringSource(old, new, filename))


@pytest.mark.parametrize("filename,wasm,old,new", CASES)
def test_half_typed_file_is_an_explicit_source_fallback(filename, wasm, old, new):
    if not (_WASM_DIR / wasm).exists():
        pytest.skip(f"Wasm not built: {wasm}")
    diff = _diff(old, new, filename)
    assert diff.is_fallback is True
    assert diff.metadata.get("fallback_reason") == "plugin_parse_error"
    assert "plugin_error" in diff.metadata
    assert diff.changes, "the edit must be preserved as source evidence"
    assert diff.is_style_only is False
    assert diff.has_semantic_changes is True


@pytest.mark.parametrize("filename,wasm,old,new", CASES[:1])
def test_parser_error_still_raises_when_fallback_is_disabled(filename, wasm, old, new):
    if not (_WASM_DIR / wasm).exists():
        pytest.skip(f"Wasm not built: {wasm}")
    from intentumdiff.plugins.exceptions import PluginOutputError, PluginParseError

    with pytest.raises(PluginParseError) as raised:
        _diff(old, new, filename, fallback=False)
    assert isinstance(raised.value, PluginOutputError), "existing handlers must still catch it"
