"""
tests/security/test_dependency_policy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Dependency policy tests for known advisory gaps that generic scanners can miss.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from intentdiff.plugins.exceptions import PluginLoadError
from intentdiff.plugins import loader


_REPO_ROOT = Path(__file__).parent.parent.parent

_PYPI_WRAPPER_ADVISORY_POLICY = {
    "wasmtime": {
        "affected_versions": {"44.0.0"},
        "advisory": "CVE-2026-44216 / GHSA-p8xm-42r7-89xg",
        "fixed_requirement": "wasmtime>=45.0",
    },
}


def _read_lockfile_versions(package: str) -> list[str]:
    """Return all pinned versions of *package* found in uv.lock."""
    lock_path = _REPO_ROOT / "uv.lock"
    if not lock_path.exists():
        return []
    text = lock_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'name\s*=\s*"{re.escape(package)}"[^\[]*?version\s*=\s*"([^"]+)"',
        re.DOTALL,
    )
    return pattern.findall(text)


def test_wasmtime_lockfile_no_longer_pins_known_vulnerable_version() -> None:
    """The normal locked dependency path must not contain the affected wrapper."""
    pinned = set(_read_lockfile_versions("wasmtime"))
    affected = _PYPI_WRAPPER_ADVISORY_POLICY["wasmtime"]["affected_versions"]

    assert not (pinned & affected)
    assert pinned


def test_wasmtime_advisory_policy_matches_loader_blocklist() -> None:
    policy = _PYPI_WRAPPER_ADVISORY_POLICY["wasmtime"]

    assert policy["advisory"] == "CVE-2026-44216 / GHSA-p8xm-42r7-89xg"
    assert policy["affected_versions"] <= loader._VULNERABLE_WASMTIME_VERSIONS
    assert policy["fixed_requirement"] == "wasmtime>=45.0"


def test_vulnerable_wasmtime_is_blocked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    monkeypatch.setattr(
        loader._importlib_metadata,
        "version",
        lambda package: "44.0.0" if package == "wasmtime" else "0.0.0",
    )

    with pytest.raises(PluginLoadError, match="known-vulnerable blocklist"):
        loader._check_wasmtime_version()


def test_vulnerable_wasmtime_blocks_first_party_plugins_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    monkeypatch.setattr(
        loader._importlib_metadata,
        "version",
        lambda package: "44.0.0" if package == "wasmtime" else "0.0.0",
    )

    with pytest.raises(PluginLoadError, match="known-vulnerable blocklist"):
        loader._check_wasmtime_version(loader._BUILTIN_WASM_DIR / "python_parser.wasm")


def test_repo_plugin_paths_are_not_first_party_trusted_exceptions() -> None:
    official_dbt_wasm = (
        _REPO_ROOT
        / "plugins"
        / "intentdiff_dbt"
        / "src"
        / "intentdiff_dbt"
        / "wasm"
        / "dbt_sql_parser.wasm"
    )

    assert not loader._is_trusted_wasm_path(official_dbt_wasm)


def test_existing_wasmtime_override_allows_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", "1")
    monkeypatch.setattr(
        loader._importlib_metadata,
        "version",
        lambda package: "44.0.0" if package == "wasmtime" else "0.0.0",
    )

    loader._check_wasmtime_version()

    assert "INTENTDIFF_ALLOW_VULNERABLE_WASMTIME is set" in caplog.text


def test_osv_advisory_gate_blocks_trusted_first_party_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    monkeypatch.setattr(loader, "_read_stamp", lambda: loader._time.time())
    monkeypatch.setattr(
        loader,
        "_load_osv_cache",
        lambda: [
            {
                "package": "wasmtime",
                "version": "44.0.0",
                "id": "GHSA-p8xm-42r7-89xg",
                "aliases": ["CVE-2026-44216"],
                "summary": "host panic",
            }
        ],
    )
    monkeypatch.setattr(
        loader._importlib_metadata,
        "version",
        lambda package: "44.0.0" if package == "wasmtime" else "0.0.0",
    )

    with pytest.raises(PluginLoadError, match="OSV advisory GHSA-p8xm-42r7-89xg"):
        loader._check_osv_cache_or_block(loader._BUILTIN_WASM_DIR / "python_parser.wasm")


def test_cached_non_blocking_osv_advisories_do_not_warn_on_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(loader, "_osv_thread_started", False)
    monkeypatch.setattr(loader, "_read_stamp", lambda: loader._time.time())
    monkeypatch.setattr(
        loader,
        "_load_osv_cache",
        lambda: [
            {
                "package": "starlette",
                "version": "1.0.0",
                "id": "PYSEC-2026-161",
                "aliases": [],
                "summary": "cached non-blocking advisory",
            }
        ],
    )

    loader._maybe_start_osv_check()

    assert "OSV advisory" not in caplog.text
    assert loader._osv_thread_started is True
