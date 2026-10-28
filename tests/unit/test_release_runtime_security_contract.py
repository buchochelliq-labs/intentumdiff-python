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


@pytest.mark.parametrize("version", ["44.0.0", "45.0.0", "46.0.1", "47.0.1", "48.0.0", "49.0.0"])
def test_known_affected_wasmtime_bindings_fail_closed(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    monkeypatch.setattr(loader._importlib_metadata, "version", lambda package: version)
    with pytest.raises(PluginSecurityError, match="known-vulnerable blocklist"):
        loader._check_wasmtime_version()
