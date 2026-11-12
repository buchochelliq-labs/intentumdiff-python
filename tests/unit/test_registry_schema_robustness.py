"""#329 (part): the canonical registry validator returns errors, never raises, and matches
patterns against the whole string."""

from __future__ import annotations

import copy
import itertools

import pytest

from intentumdiff.plugins.registry_schema import validate_registry

_VALID = {
    "version": 1,
    "plugins": {
        "intentumdiff-audit-parser": {
            "source": "git",
            "ref": "a" * 40,
            "wasm_checksums": {"audit.wasm": "b" * 64},
            "trust_tier": "official",
            "capabilities": ["parser"],
            "abi_target": "1.0.0",
        }
    },
}
_WRONG_TYPES = [None, 0, 1.5, True, "", "x", [], ["x"], [1], {}, {"a": 1}, {"a": []}]
_FIELDS = [
    "source", "ref", "repo", "description", "wasm_checksums", "dep_hashes",
    "allowed_dependencies", "trust_tier", "capabilities", "abi_target", "provenance_manifest_ref",
]


def _entry(manifest: dict) -> dict:
    return manifest["plugins"]["intentumdiff-audit-parser"]


def test_the_valid_manifest_passes() -> None:
    assert validate_registry(_VALID) == []


@pytest.mark.parametrize(("field", "value"), list(itertools.product(_FIELDS, _WRONG_TYPES)))
def test_wrong_type_entry_fields_never_raise(field, value) -> None:
    manifest = copy.deepcopy(_VALID)
    _entry(manifest)[field] = value
    assert isinstance(validate_registry(manifest), list)


@pytest.mark.parametrize("value", _WRONG_TYPES)
@pytest.mark.parametrize("field", ["version", "plugins"])
def test_wrong_type_top_level_fields_never_raise(field, value) -> None:
    manifest = copy.deepcopy(_VALID)
    manifest[field] = value
    assert isinstance(validate_registry(manifest), list)


@pytest.mark.parametrize(("field", "value"), [("source", []), ("trust_tier", {}),
                                              ("source", ["git"]), ("trust_tier", ["official"])])
def test_unhashable_enum_values_are_errors(field, value) -> None:
    manifest = copy.deepcopy(_VALID)
    _entry(manifest)[field] = value
    errors = validate_registry(manifest)
    assert any(f".{field}:" in error for error in errors), errors


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(wasm_checksums={"audit.wasm": "b" * 64 + "\n"}),
        lambda e: e.update(wasm_checksums={"audit.wasm\n": "b" * 64}),
        lambda e: e.update(dep_hashes={"pkg==1.0\n": "sha256:" + "c" * 64}),
        lambda e: e.update(dep_hashes={"pkg==1.0": "sha256:" + "c" * 64 + "\n"}),
    ],
    ids=["checksum-newline", "filename-newline", "dep-key-newline", "dep-hash-newline"],
)
def test_patterns_match_the_full_string(mutate) -> None:
    manifest = copy.deepcopy(_VALID)
    mutate(_entry(manifest))
    assert validate_registry(manifest), "a trailing newline must not satisfy a pattern"


def test_plugin_name_with_trailing_newline_is_rejected() -> None:
    manifest = copy.deepcopy(_VALID)
    manifest["plugins"]["intentumdiff-audit-parser\n"] = manifest["plugins"].pop(
        "intentumdiff-audit-parser"
    )
    assert any("package-safe" in error for error in validate_registry(manifest))
