"""#309/#329: the hub runs core's validator before building any RegistryEntry, and the typed
error carries core's ``{"path", "message"}`` errors unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from intentumdiff.plugins.exceptions import RegistryManifestError
from intentumdiff.plugins.hub import parse_registry_manifest
from intentumdiff.plugins.registry_schema import validate_registry

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "registry" / "registry-f95ceaa.yaml"
_SHA = "a" * 64


@pytest.mark.parametrize(
    ("content", "where"),
    [
        ("[]", "/: must be an object, got array"),
        ("foo", "/: must be an object, got string"),
        ("version: 1\nplugins: []\n", "/plugins: must be an object of name -> entry, got array"),
        ("version: 1\nplugins:\n  foo: null\n", "/plugins/foo: must be an object, got null"),
        ("version: 1\nplugins:\n  foo: {source: svn}\n",
         '/plugins/foo/source: "svn" is not one of'),
        ("version: 1\nplugins:\n  foo: {color: red}\n", "/plugins/foo/color: unknown field"),
        ("plugins: {}\n", "/version: required"),
        ("", "/: must be an object, got null"),
        ("version: [1\n", "not YAML"),
    ],
    ids=["list", "scalar", "plugins-list", "null-entry", "unknown-source", "unknown-field",
         "no-version", "empty", "broken-yaml"],
)
def test_malformed_manifests_raise_a_typed_error_with_the_location(content, where) -> None:
    with pytest.raises(RegistryManifestError, match="registry manifest") as caught:
        parse_registry_manifest(content)
    assert where in str(caught.value)
    assert isinstance(caught.value, RuntimeError)  # fetch_official_registry's documented error


def test_the_typed_error_carries_cores_errors_unchanged() -> None:
    with pytest.raises(RegistryManifestError) as caught:
        parse_registry_manifest("version: 1\nplugins:\n  foo: {color: red}\n")
    assert caught.value.errors[0] == {"path": "/plugins/foo/color", "message": "unknown field"}
    assert all(set(error) == {"path", "message"} for error in caught.value.errors)


def test_a_canned_core_rejection_surfaces_verbatim(monkeypatch) -> None:
    import intentumdiff.rust_core as rust_core

    canned = [{"path": "/plugins/x/ref", "message": "canned core message"}]
    monkeypatch.setattr(rust_core, "validate_registry_manifest", lambda _json: canned)
    with pytest.raises(RegistryManifestError, match="/plugins/x/ref: canned core") as caught:
        parse_registry_manifest("version: 1\nplugins: {}\n")
    assert caught.value.errors == canned


def test_a_manifest_without_a_json_form_is_a_typed_error() -> None:
    with pytest.raises(RegistryManifestError, match="not JSON-representable"):
        parse_registry_manifest("version: 1\nplugins: {}\nwhen: 2026-01-01\n")


def test_loader_and_ci_validator_accept_the_same_published_manifest() -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    assert validate_registry(yaml.safe_load(content)) == []
    entries = parse_registry_manifest(content)
    assert len(entries) == len(yaml.safe_load(content)["plugins"])


def test_a_valid_entry_is_converted_unchanged() -> None:
    content = yaml.safe_dump({"version": 1, "plugins": {"dbt": {
        "source": "pypi", "ref": "1.0.0", "wasm_checksums": {"dbt.wasm": _SHA},
        "capabilities": ["parser"], "abi_target": "1.0.0",
    }}})
    entry = parse_registry_manifest(content)["dbt"]
    assert (entry.source, entry.ref, entry.wasm_checksums) == ("pypi", "1.0.0", {"dbt.wasm": _SHA})
