"""#309: the hub runs the canonical validator before building any RegistryEntry."""

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
        ("[]", "top level must be a mapping"),
        ("foo", "top level must be a mapping"),
        ("version: 1\nplugins: []\n", "registry.plugins"),
        ("version: 1\nplugins:\n  foo: null\n", "plugins['foo']: entry must be a mapping"),
        ("version: 1\nplugins:\n  foo: {source: svn}\n", "plugins['foo'].source"),
        ("version: 1\nplugins:\n  foo: {color: red}\n", "unknown field(s): color"),
        ("plugins: {}\n", "registry.version"),
        ("", "top level must be a mapping"),
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


def test_loader_and_ci_validator_accept_the_same_published_manifest() -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    assert validate_registry(yaml.safe_load(content)) == []
    entries = parse_registry_manifest(content)
    assert len(entries) == len(yaml.safe_load(content)["plugins"])


def test_a_valid_entry_is_converted_unchanged() -> None:
    content = yaml.safe_dump({"version": 1, "plugins": {"dbt": {
        "source": "pypi", "ref": "1.0.0", "wasm_checksums": {"dbt.wasm": _SHA},
    }}})
    entry = parse_registry_manifest(content)["dbt"]
    assert (entry.source, entry.ref, entry.wasm_checksums) == ("pypi", "1.0.0", {"dbt.wasm": _SHA})
