"""Regression: plugin manifest provenance cannot be erased by a failed load (#252).

These exercise the real CLI host-I/O path.  No Rust semantic logic is mirrored.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from intentumdiff.cli._commands import _cmd_plugins_add
from intentumdiff.plugins import hub


def _args(path: Path, *, save: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        plugin_name="fresh",
        source="pypi",
        ref="1.0.0",
        repo="",
        skip_verify=True,
        yes=True,
        save=save,
        file=str(path),
    )


def _mock_install(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def pip_install(target: str, spec: hub.PluginSpec) -> int:
        calls.append(target)
        return 0

    monkeypatch.setattr(hub, "pip_install", pip_install)
    return calls


def test_add_refuses_malformed_existing_manifest_before_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "intentumdiff_plugins.yaml"
    original = b"version: 1\nplugins: [existing\n"
    manifest.write_bytes(original)
    calls = _mock_install(monkeypatch)

    with pytest.raises(RuntimeError, match="Refusing to overwrite invalid plugins manifest"):
        _cmd_plugins_add(_args(manifest))

    assert calls == []
    assert manifest.read_bytes() == original


def test_add_preserves_existing_pinned_wasm_manifest_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "intentumdiff_plugins.yaml"
    old = hub.PluginSpec(
        name="existing",
        source="pypi",
        ref="1.2.3",
        wasm_checksums={"existing_parser.wasm": "a" * 64},
    )
    hub.save_plugins_file(manifest, [old])
    calls = _mock_install(monkeypatch)

    _cmd_plugins_add(_args(manifest))

    assert len(calls) == 1
    specs = hub.load_plugins_file(manifest)
    assert [spec.name for spec in specs] == ["existing", "fresh"]
    assert specs[0].wasm_checksums == {"existing_parser.wasm": "a" * 64}


def test_add_without_save_does_not_read_or_replace_existing_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "intentumdiff_plugins.yaml"
    original = b"version: 1\nplugins: [existing\n"
    manifest.write_bytes(original)
    calls = _mock_install(monkeypatch)

    _cmd_plugins_add(_args(manifest, save=False))

    assert len(calls) == 1
    assert manifest.read_bytes() == original


def test_add_refuses_broken_manifest_symlink_before_install_where_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows runners may disallow symlinks; the malformed-manifest gate above
    # is exercised on all four CI platforms without introducing test skips.
    if os.name != "nt":
        manifest = tmp_path / "intentumdiff_plugins.yaml"
        missing_target = tmp_path / "missing.yaml"
        manifest.symlink_to(missing_target)
        calls = _mock_install(monkeypatch)

        with pytest.raises(RuntimeError, match="Refusing to overwrite invalid plugins manifest"):
            _cmd_plugins_add(_args(manifest))

        assert calls == []
        assert manifest.is_symlink()
        assert not missing_target.exists()
