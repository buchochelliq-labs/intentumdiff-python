"""Plugin removal must reconcile immutable manifest pins or explicitly fail (#257)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from intentumdiff.cli._commands import _cmd_plugins_remove
from intentumdiff.plugins import hub


def _args(manifest: Path, *, save: bool = True) -> argparse.Namespace:
    return argparse.Namespace(plugin_name="remove-me", file=str(manifest), save=save)


def _mock_uninstall(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_uninstall(package: str) -> int:
        calls.append(package)
        return 0

    monkeypatch.setattr(hub, "pip_uninstall", fake_uninstall)
    return calls


def test_remove_rejects_malformed_manifest_before_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "intentumdiff_plugins.yaml"
    raw = b"version: 1\nplugins: [existing\n"
    path.write_bytes(raw)
    calls = _mock_uninstall(monkeypatch)

    with pytest.raises(RuntimeError, match="Refusing to uninstall with invalid plugins manifest"):
        _cmd_plugins_remove(_args(path))

    assert calls == []
    assert path.read_bytes() == raw


def test_remove_preserves_other_wasm_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "intentumdiff_plugins.yaml"
    pinned = hub.PluginSpec(
        name="retain-me", source="pypi", ref="1.0",
        wasm_checksums={"parser.wasm": "f" * 64},
    )
    removed = hub.PluginSpec(name="remove-me", source="pypi", ref="2.0")
    hub.save_plugins_file(path, [pinned, removed])
    calls = _mock_uninstall(monkeypatch)

    _cmd_plugins_remove(_args(path))

    assert len(calls) == 1
    assert [x.name for x in hub.load_plugins_file(path)] == ["retain-me"]
    assert hub.load_plugins_file(path)[0].wasm_checksums == {"parser.wasm": "f" * 64}


def test_remove_without_save_does_not_read_invalid_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "intentumdiff_plugins.yaml"
    raw = b"version: 1\nplugins: [existing\n"
    path.write_bytes(raw)
    calls = _mock_uninstall(monkeypatch)

    _cmd_plugins_remove(_args(path, save=False))

    assert len(calls) == 1
    assert path.read_bytes() == raw


def test_remove_broken_symlink_preflight_on_posix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if os.name != "nt":
        path = tmp_path / "intentumdiff_plugins.yaml"
        target = tmp_path / "missing.yaml"
        path.symlink_to(target)
        calls = _mock_uninstall(monkeypatch)
        with pytest.raises(RuntimeError, match="Refusing to uninstall with invalid plugins manifest"):
            _cmd_plugins_remove(_args(path))
        assert calls == []
        assert path.is_symlink()
        assert not target.exists()


def test_remove_surfaces_partial_failure_after_pip_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "intentumdiff_plugins.yaml"
    hub.save_plugins_file(path, [hub.PluginSpec(name="remove-me")])
    calls = _mock_uninstall(monkeypatch)

    def write_failed(*args):
        raise PermissionError("read-only manifest")

    monkeypatch.setattr(hub, "save_plugins_file", write_failed)

    with pytest.raises(RuntimeError, match="Plugin uninstalled but could not update"):
        _cmd_plugins_remove(_args(path))

    assert len(calls) == 1
    assert [x.name for x in hub.load_plugins_file(path)] == ["remove-me"]
