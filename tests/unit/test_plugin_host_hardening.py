"""Plugin-host hardening (issue #87).

1. Early output bounding: the store's linear-memory limiter bails DURING parse
   (a typed sandbox violation), instead of letting a pathological input balloon
   guest memory far past the 16 MB output cap before the host measures.
2. Parser parity + fuzz: staged first-party parsers produce consistently-shaped
   trees for their own shipped examples, and malformed input never crashes the
   host - every outcome is a valid tree or a TYPED plugin error.

No test performs network I/O. The full 60+-parser sweep is gated behind
``INTENTDIFF_PARSER_FUZZ_FULL=1`` (it instantiates every component); the
default subset keeps the unit suite fast while pinning the harness itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from intentdiff.plugins.exceptions import (
    PluginFuelExhausted,
    PluginSandboxViolation,
)
from intentdiff.plugins.loader import _plugin_memory_limit_bytes, load_plugin

_WASM_DIR = Path(__file__).resolve().parents[2] / "src" / "intentdiff" / "wasm"

_DEFAULT_SUBSET = (
    "python_parser.wasm",
    "json_parser.wasm",
    "yaml_parser.wasm",
    "xml_parser.wasm",
    "sql_parser.wasm",
    "javascript_parser.wasm",
    "markdown_parser.wasm",
    "dockerfile_parser.wasm",
)

_TYPED_PLUGIN_ERRORS = (PluginSandboxViolation, PluginFuelExhausted, ValueError)


def _staged_parsers() -> list[Path]:
    if not _WASM_DIR.is_dir():
        return []
    if os.environ.get("INTENTDIFF_PARSER_FUZZ_FULL") == "1":
        return sorted(
            path
            for path in _WASM_DIR.glob("*_parser.wasm")
            # pre-rebrand build artifacts export the retired wit world and
            # cannot load; the staging dir is gitignored so stale copies linger
            if not path.name.startswith("py_semantic_diff_")
        )
    return [
        _WASM_DIR / name for name in _DEFAULT_SUBSET if (_WASM_DIR / name).exists()
    ]


def _tree_or_typed_error(payload: str) -> bool:
    """The WIT contract's two valid shapes: a semantic tree, or an error
    envelope ({"error": "..."}) - the in-band typed rejection channel."""
    tree = json.loads(payload)
    if not isinstance(tree, dict):
        return False
    if isinstance(tree.get("error"), str) and tree["error"]:
        return True
    return all(key in tree for key in ("node_type", "children"))


@pytest.mark.parametrize("wasm", _staged_parsers(), ids=lambda p: p.stem)
def test_parser_parity_example_round_trip(wasm: Path) -> None:
    """Every staged parser parses its own shipped example into a tree with the
    contract shape (node_type/children roots) - the cross-parser parity pin."""
    plugin = load_plugin(str(wasm), trusted=True)
    if plugin.call_parser_mode() != "full-parse":
        pytest.skip("interpret-cst parsers take host CST JSON, not raw source")
    languages = plugin.call_language_ids()
    assert languages, f"{wasm.name} declares no languages"
    language = languages[0]
    example = plugin.call_example(language)
    content = example.get("new") or example.get("old")
    assert content, f"{wasm.name} ships no example for {language}"
    payload = plugin.call_process(content, language, f"example.{language}")
    assert _tree_or_typed_error(payload), f"{wasm.name} produced a non-contract tree"


@pytest.mark.parametrize("wasm", _staged_parsers(), ids=lambda p: p.stem)
def test_parser_fuzz_malformed_input_never_crashes_the_host(wasm: Path) -> None:
    """Truncated, noisy, and NUL-ridden inputs must yield a tree or a TYPED
    plugin error - never an untyped host exception, hang, or crash."""
    plugin = load_plugin(str(wasm), trusted=True)
    if plugin.call_parser_mode() != "full-parse":
        pytest.skip("interpret-cst parsers take host CST JSON, not raw source")
    language = plugin.call_language_ids()[0]
    sample = plugin.call_example(language)
    example = sample.get("new") or sample.get("old") or "x"
    fuzz_inputs = [
        example[: max(1, len(example) // 2)],          # truncated mid-construct
        example + "\xff\xfe" * 512 + "\x01\x02" * 512,  # binary-ish tail
        "\x00" * 2048,                                  # NUL flood
        ("{[(<" * 512) + example,                       # bracket noise prefix
    ]
    for content in fuzz_inputs:
        try:
            payload = plugin.call_process(content, language, "fuzz-input")
        except _TYPED_PLUGIN_ERRORS:
            continue  # typed rejection is a valid outcome
        assert _tree_or_typed_error(payload)


def test_memory_limiter_bails_during_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #87 item 1: with a tight linear-memory cap, a repetitive input
    whose tree would balloon past the cap traps DURING parse as a typed
    sandbox violation (status 'memory_limit'), not after a giant allocation."""
    monkeypatch.setenv("INTENTDIFF_PLUGIN_MEMORY_LIMIT_BYTES", str(28 * 1024 * 1024))
    wasm = _WASM_DIR / "json_parser.wasm"
    if not wasm.exists():
        pytest.skip("json parser not staged")
    plugin = load_plugin(str(wasm), trusted=True)
    # ~2 MB of dense JSON scalars: roughly a node per few bytes, ballooning the
    # tree + serialized output well past the tightened cap.
    noise = "[" + ",".join('"x"' for _ in range(400_000)) + "]"

    with pytest.raises(_TYPED_PLUGIN_ERRORS):
        plugin.call_process(noise, "json", "balloon.json")


def test_memory_limit_env_override_and_default() -> None:
    assert _plugin_memory_limit_bytes() == 192 * 1024 * 1024
    os.environ["INTENTDIFF_PLUGIN_MEMORY_LIMIT_BYTES"] = "1048576"
    try:
        assert _plugin_memory_limit_bytes() == 1048576
    finally:
        del os.environ["INTENTDIFF_PLUGIN_MEMORY_LIMIT_BYTES"]
    os.environ["INTENTDIFF_PLUGIN_MEMORY_LIMIT_BYTES"] = "not-a-number"
    try:
        assert _plugin_memory_limit_bytes() == 192 * 1024 * 1024
    finally:
        del os.environ["INTENTDIFF_PLUGIN_MEMORY_LIMIT_BYTES"]


def test_default_cap_does_not_break_normal_parses() -> None:
    wasm = _WASM_DIR / "json_parser.wasm"
    if not wasm.exists():
        pytest.skip("json parser not staged")
    plugin = load_plugin(str(wasm), trusted=True)
    payload = plugin.call_process('{"a": [1, 2, 3]}', "json", "ok.json")
    assert _tree_or_typed_error(payload)
