"""Packaging catalogue completeness; semantic selection remains Rust-owned."""
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).parents[2]


def test_manifest_includes_every_enabled_parser_entry_point():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    enabled = set(project['project']['entry-points']['intentumdiff.parsers'])
    manifest = json.loads((ROOT / 'src/intentumdiff/wasm/parser_manifest.json').read_text())
    assert enabled <= manifest['parsers'].keys(), sorted(enabled - manifest['parsers'].keys())
    assert 'freebasic' not in manifest['parsers']


def test_special_filename_and_alias_discovery_hints():
    manifest = json.loads((ROOT / 'src/intentumdiff/wasm/parser_manifest.json').read_text())
    for filename, language in [('CMakeLists.txt', 'cmake'), ('go.mod', 'gomod'),
                               ('Makefile', 'make'), ('GNUmakefile', 'make'),
                               ('.toml', 'toml'), ('.proto', 'proto'), ('.ini', 'ini'),
                               ('.wast', 'wast')]:
        assert manifest['extension_index'][filename] == language
        assert filename in manifest['parsers'][language]['extensions']
    assert manifest['parsers']['wast']['wasm'] == manifest['parsers']['wat']['wasm']
    assert manifest['parsers']['databricks']['wasm'] == manifest['parsers']['databricks-workflow']['wasm']
    # Workflow aliases must not claim arbitrary Python or SQL notebook sources.
    assert manifest['parsers']['databricks']['extensions'] == []
