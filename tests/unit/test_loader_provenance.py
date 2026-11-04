"""First-party bundled Wasm provenance is required and fail-closed at runtime.

Synthetic byte fixtures ensure the exact verified buffer is returned for
compilation, with no missing-manifest, malformed-manifest, or checksum waiver.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from intentumdiff.plugins import loader
from intentumdiff.plugins.exceptions import PluginLoadError


def _setup(monkeypatch, tmp_path: Path, *, wasm: dict[str, bytes], manifest: dict | None) -> Path:
    wasm_dir = (tmp_path / "wasm").resolve()
    wasm_dir.mkdir()
    for name, data in wasm.items():
        (wasm_dir / name).write_bytes(data)
    manifest_path = wasm_dir / "wasm_provenance.json"
    if manifest is not None:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(loader, "_BUILTIN_WASM_DIR", wasm_dir)
    monkeypatch.setattr(loader, "_PROVENANCE_MANIFEST", manifest_path)
    return wasm_dir


def _manifest(wasm: dict[str, bytes]) -> dict:
    return {
        "schema_version": 1,
        "artifacts": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for name, data in wasm.items()
        },
    }


def test_matching_bundled_wasm_returns_exact_approved_bytes(monkeypatch, tmp_path, caplog):
    wasm = {"a_parser.wasm": b"\\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=_manifest(wasm))
    assert loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm") == wasm["a_parser.wasm"]
    assert "provenance" not in caplog.text.lower()


def test_missing_manifest_blocks_bundled_wasm_even_without_enforcement_flag(monkeypatch, tmp_path):
    wasm = {"a_parser.wasm": b"\\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=None)
    with pytest.raises(PluginLoadError, match="manifest is missing or invalid"):
        loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")


def test_non_bundled_path_uses_separate_third_party_provenance(monkeypatch, tmp_path):
    wasm = {"a_parser.wasm": b"\\x00asm A"}
    _setup(monkeypatch, tmp_path, wasm=wasm, manifest=None)
    outside = tmp_path / "elsewhere" / "third_party.wasm"
    outside.parent.mkdir()
    outside.write_bytes(b"whatever")
    assert loader._verify_builtin_provenance(outside) is None


def test_tampered_bundled_wasm_blocks_by_default(monkeypatch, tmp_path):
    wasm = {"a_parser.wasm": b"\\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=_manifest(wasm))
    (wasm_dir / "a_parser.wasm").write_bytes(b"TAMPERED")
    with pytest.raises(PluginLoadError, match="provenance SHA-256/size"):
        loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")


def test_unlisted_bundled_wasm_blocks_by_default(monkeypatch, tmp_path):
    listed = {"a_parser.wasm": b"\\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=listed, manifest=_manifest(listed))
    (wasm_dir / "py_semantic_diff_stale.wasm").write_bytes(b"stale")
    with pytest.raises(PluginLoadError, match="not listed in the provenance manifest"):
        loader._verify_builtin_provenance(wasm_dir / "py_semantic_diff_stale.wasm")


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("json", "manifest is missing or invalid"),
        ("schema", "invalid schema"),
        ("artifacts", "lacks artifacts map"),
        ("digest", "invalid provenance metadata"),
        ("size", "provenance SHA-256/size"),
        ("missing_entry", "not listed"),
    ],
)
def test_corrupted_manifest_never_allows_execution(monkeypatch, tmp_path, damage, message):
    wasm = {"a_parser.wasm": b"\\x00asm A"}
    manifest = _manifest(wasm)
    if damage == "schema":
        manifest["schema_version"] = 99
    elif damage == "artifacts":
        manifest["artifacts"] = None
    elif damage == "digest":
        manifest["artifacts"]["a_parser.wasm"]["sha256"] = "not-a-digest"
    elif damage == "size":
        manifest["artifacts"]["a_parser.wasm"]["size_bytes"] += 1
    elif damage == "missing_entry":
        manifest["artifacts"] = {}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=manifest)
    if damage == "json":
        (wasm_dir / "wasm_provenance.json").write_text("{malformed")
    with pytest.raises(PluginLoadError, match=message):
        loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")
