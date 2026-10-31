"""Fail-closed release-candidate Wasmtime provenance and no CI waiver.

This checks that CI cannot silently bypass the security policy while claiming
installed-wheel or source-judged parser acceptance.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from intentumdiff.plugins import loader
from intentumdiff.plugins.exceptions import PluginSecurityError


_ROOT = Path(__file__).resolve().parents[2]


def test_python_host_uses_exact_reviewed_binding() -> None:
    metadata = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = metadata["project"]["dependencies"]
    assert [entry for entry in dependencies if entry.startswith("wasmtime")] == ["wasmtime==50.0.1"]


def test_release_ci_and_fixtures_cannot_autowaive_wasmtime_security() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    fixtures = (_ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME" not in workflow
    assert "INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME" not in fixtures


@pytest.mark.parametrize("version", [
    "36.0.18", "37.0.0", "44.0.0", "45.0.0", "46.0.1",
    "47.0.1", "47.0.3", "48.0.0", "48.0.3", "48.0.6",
    "49.0.0", "49.0.3", "48.0.7rc1", "unparseable",
])
def test_known_affected_wasmtime_bindings_fail_closed(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    monkeypatch.setattr(loader._importlib_metadata, "version", lambda package: version)
    with pytest.raises(PluginSecurityError, match="known-vulnerable blocklist"):
        loader._check_wasmtime_version()


@pytest.mark.parametrize("version", ["36.0.19", "48.0.7", "49.0.4", "50.0.1"])
def test_vendor_patched_wasmtime_releases_are_allowed(
    monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    monkeypatch.setattr(loader._importlib_metadata, "version", lambda package: version)
    loader._check_wasmtime_version()


def test_missing_wasmtime_distribution_metadata_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_package: str) -> str:
        raise loader._importlib_metadata.PackageNotFoundError("wasmtime")
    monkeypatch.setattr(loader._importlib_metadata, "version", missing)
    with pytest.raises(PluginSecurityError, match="metadata is absent"):
        loader._check_wasmtime_version()
