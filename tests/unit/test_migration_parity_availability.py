"""The 74-language release gate verifies staged bytes, never manifest promises."""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from intentumdiff import rust_core


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_migration_parity.py"
CASE = {"id": "corpus-powershell-addition", "corpus_language": "powershell", "filename": "deploy.ps1"}


def _checker():
    return runpy.run_path(str(SCRIPT))["check_available_corpus_parser"]


def _staged(tmp_path, content: bytes):
    (tmp_path / "parser_manifest.json").write_text(json.dumps({
        "parsers": {"powershell": {"plugin_id": "powershell", "wasm": "powershell_parser.wasm"}}
    }), encoding="utf-8")
    (tmp_path / "powershell_parser.wasm").write_bytes(content)


def test_manifest_and_exact_bytes_must_reach_rust_for_architecture_override(tmp_path, monkeypatch):
    data = b"the staged source of truth"
    _staged(tmp_path, data)
    expected = hashlib.sha256(data).hexdigest()
    requests = []

    def rust(operation, *args):
        requests.append((operation, args))
        if operation == "parser_availability":
            return {"reason": "legacy exclusion", "unavailable": ["powershell", "legacy exclusion"]}
        assert operation == "parser_availability_with_digest"
        assert args == ("powershell", "deploy.ps1", "Windows", "ARM64", expected)
        return {"reason": None, "unavailable": None}

    monkeypatch.setattr(rust_core, "_c_abi_call", rust)
    _checker()(CASE, system="Windows", machine="ARM64", wasm_dir=tmp_path)
    assert [op for op, _ in requests] == ["parser_availability", "parser_availability_with_digest"]


@pytest.mark.parametrize("variant", ["absent_manifest", "missing_wasm", "wrong_bytes"])
def test_unavailable_component_never_certifies_a_language(tmp_path, monkeypatch, variant):
    if variant != "absent_manifest":
        _staged(tmp_path, b"not the vetted artifact")
    if variant == "missing_wasm":
        (tmp_path / "powershell_parser.wasm").unlink()
    calls = []

    def rust(operation, *args):
        calls.append((operation, args))
        if operation == "parser_availability":
            return {"reason": "legacy exclusion", "unavailable": ["powershell", "legacy exclusion"]}
        assert operation == "parser_availability_with_digest"
        assert variant != "absent_manifest"
        if variant == "missing_wasm":
            assert args[-1] == "", "a missing file must never use a declared registry checksum"
        return {"reason": "unverified", "unavailable": ["powershell", "unverified"]}

    monkeypatch.setattr(rust_core, "_c_abi_call", rust)
    with pytest.raises(AssertionError, match="semantic parser unavailable"):
        _checker()(CASE, system="Windows", machine="ARM64", wasm_dir=tmp_path)
    assert calls and calls[0][0] == "parser_availability"
