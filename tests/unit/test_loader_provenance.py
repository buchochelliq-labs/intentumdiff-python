"""#89 loader-side provenance verification — verify a bundled first-party wasm against the
provenance manifest before load. Uses a monkeypatched temp wasm dir so it never touches the
real bundled parsers, and is independent of whether the dev tree has a manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from intentdiff.plugins import loader
from intentdiff.plugins.exceptions import PluginLoadError

_ENFORCE = loader._ENFORCE_PROVENANCE_ENV


def _setup(monkeypatch, tmp_path: Path, *, wasm: dict[str, bytes], manifest: dict | None) -> Path:
    """Point the loader's bundled-wasm dir + manifest at a temp dir and populate it."""
    wasm_dir = (tmp_path / "wasm").resolve()
    wasm_dir.mkdir()
    for name, data in wasm.items():
        (wasm_dir / name).write_bytes(data)
    manifest_path = wasm_dir / "wasm_provenance.json"
    if manifest is not None:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(loader, "_BUILTIN_WASM_DIR", wasm_dir)
    monkeypatch.setattr(loader, "_PROVENANCE_MANIFEST", manifest_path)
    monkeypatch.delenv(_ENFORCE, raising=False)
    return wasm_dir


def _manifest(wasm: dict[str, bytes]) -> dict:
    return {
        "schema_version": 1,
        "artifacts": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for name, data in wasm.items()
        },
    }


def test_matching_bundled_wasm_passes_silently(monkeypatch, tmp_path, caplog) -> None:
    wasm = {"a_parser.wasm": b"\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=_manifest(wasm))
    loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")  # no raise
    assert "provenance" not in caplog.text.lower()


def test_absent_manifest_is_skipped(monkeypatch, tmp_path) -> None:
    wasm = {"a_parser.wasm": b"\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=None)
    # No manifest -> optional verification is a no-op even under enforcement.
    monkeypatch.setenv(_ENFORCE, "1")
    loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")  # no raise


def test_non_bundled_path_is_ignored(monkeypatch, tmp_path) -> None:
    wasm = {"a_parser.wasm": b"\x00asm A"}
    _setup(monkeypatch, tmp_path, wasm=wasm, manifest=_manifest(wasm))
    outside = tmp_path / "elsewhere" / "third_party.wasm"
    outside.parent.mkdir()
    outside.write_bytes(b"whatever")
    monkeypatch.setenv(_ENFORCE, "1")
    loader._verify_builtin_provenance(outside)  # not first-party -> skipped, no raise


def test_tampered_bundled_wasm_warns_by_default(monkeypatch, tmp_path, caplog) -> None:
    wasm = {"a_parser.wasm": b"\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=_manifest(wasm))
    (wasm_dir / "a_parser.wasm").write_bytes(b"TAMPERED")  # same name, different bytes
    import logging

    with caplog.at_level(logging.WARNING):
        loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")  # warns, no raise
    assert "provenance" in caplog.text.lower()
    assert "SHA-256" in caplog.text


def test_tampered_bundled_wasm_blocks_under_enforcement(monkeypatch, tmp_path) -> None:
    wasm = {"a_parser.wasm": b"\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=wasm, manifest=_manifest(wasm))
    (wasm_dir / "a_parser.wasm").write_bytes(b"TAMPERED")
    monkeypatch.setenv(_ENFORCE, "1")
    with pytest.raises(PluginLoadError, match="provenance SHA-256"):
        loader._verify_builtin_provenance(wasm_dir / "a_parser.wasm")


def test_unlisted_bundled_wasm_blocks_under_enforcement(monkeypatch, tmp_path) -> None:
    # The #87 stale-artifact case: a bundled wasm the manifest never saw.
    listed = {"a_parser.wasm": b"\x00asm A"}
    wasm_dir = _setup(monkeypatch, tmp_path, wasm=listed, manifest=_manifest(listed))
    (wasm_dir / "py_semantic_diff_stale.wasm").write_bytes(b"stale")
    monkeypatch.setenv(_ENFORCE, "1")
    with pytest.raises(PluginLoadError, match="not listed in the provenance manifest"):
        loader._verify_builtin_provenance(wasm_dir / "py_semantic_diff_stale.wasm")
