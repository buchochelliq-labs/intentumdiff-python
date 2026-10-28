"""Rust-owned Windows ARM64 Wasm guard: Python hashes and transports only."""
from __future__ import annotations

import hashlib

import pytest

from intentumdiff import rust_core
from intentumdiff.plugins import loader, registry
from intentumdiff.plugins.exceptions import PluginLoadError


def test_loader_hashes_staged_bytes_only_after_rust_flags_a_component(tmp_path, monkeypatch):
    wasm = tmp_path / "powershell_parser.wasm"
    raw = b"exact staged Wasm content"
    wasm.write_bytes(raw)
    expected = hashlib.sha256(raw).hexdigest()
    calls = []

    def rust(operation, *args):
        assert operation == "parser_component_availability"
        calls.append(args)
        assert args[0] == wasm.name
        return {"reason": "unverified"} if args[3] == "" else (
            {"reason": None} if args[3] == expected else {"reason": "wrong digest"}
        )

    monkeypatch.setattr(rust_core, "_c_abi_call", rust)
    loader._check_rust_component_load_policy(wasm)
    assert [args[3] for args in calls] == ["", expected]


def test_loader_refuses_unverified_component_even_when_path_exists(tmp_path, monkeypatch):
    wasm = tmp_path / "powershell_parser.wasm"
    wasm.write_bytes(b"wrong parser bytes")
    monkeypatch.setattr(rust_core, "_c_abi_call", lambda _operation, *args: {"reason": "unverified"})
    with pytest.raises(PluginLoadError):
        loader._check_rust_component_load_policy(wasm)


def test_loader_does_not_hash_unrestricted_components(tmp_path, monkeypatch):
    wasm = tmp_path / "unrelated_parser.wasm"
    monkeypatch.setattr(rust_core, "_c_abi_call", lambda _operation, *args: {"reason": None})
    # No file was created: unrestricted components do not require a SHA-256
    # before the normal loader's later existence/provenance checks.
    loader._check_rust_component_load_policy(wasm)


def test_loader_rejects_malformed_rust_response_and_missing_bytes(tmp_path, monkeypatch):
    wasm = tmp_path / "powershell_parser.wasm"
    monkeypatch.setattr(rust_core, "_c_abi_call", lambda _operation, *args: {"unexpected": True})
    with pytest.raises(PluginLoadError, match="malformed Rust"):
        loader._check_rust_component_load_policy(wasm)
    monkeypatch.setattr(rust_core, "_c_abi_call", lambda _operation, *args: {"reason": "verify"})
    with pytest.raises(PluginLoadError, match="cannot hash staged"):
        loader._check_rust_component_load_policy(wasm)


def test_registry_passes_actual_host_digest_to_rust_without_python_policy(tmp_path, monkeypatch):
    wasm = tmp_path / "powershell_parser.wasm"
    wasm.write_bytes(b"staged candidate")
    digest = hashlib.sha256(wasm.read_bytes()).hexdigest()
    calls = []

    def rust(operation, *args):
        calls.append((operation, args))
        if operation == "parser_availability":
            return {"reason": "legacy Windows ARM64 restriction", "unavailable": None}
        assert operation == "parser_availability_with_digest"
        assert args[4] == digest
        return {"reason": None, "unavailable": None}

    monkeypatch.setattr(rust_core, "_c_abi_call", rust)
    assert registry.arch_incompatible_reason("powershell") == "legacy Windows ARM64 restriction"
    assert registry.arch_incompatible_reason("powershell", wasm) is None
    assert [name for name, _ in calls] == [
        "parser_availability", "parser_availability", "parser_availability_with_digest",
    ]
    calls.clear()
    assert registry.arch_incompatible_reason("powershell", tmp_path / "missing.wasm")
    assert [name for name, _ in calls] == ["parser_availability"]
