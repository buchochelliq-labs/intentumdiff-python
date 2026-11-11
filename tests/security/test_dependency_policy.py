"""
tests/security/test_dependency_policy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Dependency policy tests for known advisory gaps that generic scanners can miss.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from intentumdiff.plugins.exceptions import PluginLoadError
from intentumdiff.plugins import loader


_REPO_ROOT = Path(__file__).parent.parent.parent

_PYPI_WRAPPER_ADVISORY_POLICY = {
    "wasmtime": {
        "affected_versions": {"44.0.0"},
        "advisory": "CVE-2026-44216 / GHSA-p8xm-42r7-89xg",
        "fixed_requirement": "wasmtime>=45.0",
    },
}


def _pinned_versions(package: str) -> list[str]:
    """Exact ``package==X`` pins in pyproject.toml's runtime dependencies.

    The repository has no lockfile: pyproject.toml is the release input. CI installs it
    (``pip install -e .[dev,serve]``) and the release workflow builds the wheel from it, so this
    test reads the same pins that ship (#325). A missing pyproject is a release-input error.
    """
    pyproject = _REPO_ROOT / "pyproject.toml"
    assert pyproject.is_file(), f"release input missing: {pyproject}"
    dependencies = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    pattern = re.compile(rf"^{re.escape(package)}\s*==\s*([^\s;,]+)\s*(?:;.*)?$", re.IGNORECASE)
    return [m.group(1) for dep in dependencies if (m := pattern.match(dep.strip()))]


def test_wasmtime_release_pin_is_exact_and_not_known_vulnerable() -> None:
    """The shipped dependency set pins one exact wrapper version outside the blocklist."""
    pinned = _pinned_versions("wasmtime")
    policy = _PYPI_WRAPPER_ADVISORY_POLICY["wasmtime"]

    assert len(pinned) == 1, f"pyproject.toml must pin wasmtime exactly (==), found {pinned}"
    assert pinned[0] not in policy["affected_versions"]
    assert pinned[0] not in loader._VULNERABLE_WASMTIME_VERSIONS
    major = int(pinned[0].split(".")[0])
    assert major >= int(policy["fixed_requirement"].split(">=")[1].split(".")[0])


def test_wasmtime_advisory_policy_matches_loader_blocklist() -> None:
    policy = _PYPI_WRAPPER_ADVISORY_POLICY["wasmtime"]

    assert policy["advisory"] == "CVE-2026-44216 / GHSA-p8xm-42r7-89xg"
    assert policy["affected_versions"] <= loader._VULNERABLE_WASMTIME_VERSIONS
    assert policy["fixed_requirement"] == "wasmtime>=45.0"


def test_vulnerable_wasmtime_is_blocked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
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
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
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
        / "intentumdiff_dbt"
        / "src"
        / "intentumdiff_dbt"
        / "wasm"
        / "dbt_sql_parser.wasm"
    )

    assert not loader._is_trusted_wasm_path(official_dbt_wasm)


def test_existing_wasmtime_override_allows_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", "1")
    monkeypatch.setattr(
        loader._importlib_metadata,
        "version",
        lambda package: "44.0.0" if package == "wasmtime" else "0.0.0",
    )

    loader._check_wasmtime_version()

    assert "INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME is set" in caplog.text


def test_osv_advisory_gate_blocks_trusted_first_party_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
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
