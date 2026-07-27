"""Provenance manifest generator + gate (issue #89). Synthetic fixtures — independent of the
gitignored real wasm set, so the logic is verified even in a checkout without built parsers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# The module lives under scripts/ (not an installed package); load it by path. It must be
# registered in sys.modules BEFORE exec_module so its frozen dataclass can resolve its own
# annotations (the dataclass machinery looks the module up via sys.modules[__module__]).
_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "wasm_provenance.py"
_spec = importlib.util.spec_from_file_location("wasm_provenance", _MODULE_PATH)
assert _spec and _spec.loader
wasm_provenance = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = wasm_provenance
_spec.loader.exec_module(wasm_provenance)


def _wasm_dir(tmp_path: Path, files: dict[str, bytes]) -> Path:
    d = tmp_path / "wasm"
    d.mkdir()
    for name, data in files.items():
        (d / name).write_bytes(data)
    return d


def test_generate_manifest_captures_sha256_size_and_ignores_non_wasm(tmp_path: Path) -> None:
    d = _wasm_dir(
        tmp_path,
        {
            "python_parser.wasm": b"\x00asm python",
            "css_parser.wasm": b"\x00asm css bytes",
            "parser_manifest.json": b"{}",  # a sibling non-wasm file must be ignored
        },
    )
    manifest = wasm_provenance.generate_manifest(d, built_from_commit="abc123")

    assert manifest["schema_version"] == wasm_provenance.SCHEMA_VERSION
    assert manifest["built_from_commit"] == "abc123"
    assert manifest["artifact_count"] == 2
    assert set(manifest["artifacts"]) == {"python_parser.wasm", "css_parser.wasm"}
    entry = manifest["artifacts"]["python_parser.wasm"]
    assert entry["sha256"] == hashlib.sha256(b"\x00asm python").hexdigest()
    assert entry["size_bytes"] == len(b"\x00asm python")


def test_generate_verify_round_trips(tmp_path: Path) -> None:
    d = _wasm_dir(tmp_path, {"a_parser.wasm": b"aaaa", "b_parser.wasm": b"bbbbbb"})
    manifest = wasm_provenance.generate_manifest(d)
    # Exact match -> no raise.
    wasm_provenance.verify_manifest(d, manifest)
    assert wasm_provenance.diff_manifest(d, manifest).ok


def test_verify_rejects_a_stale_extra_artifact(tmp_path: Path) -> None:
    """The #87 pre-rebrand case: a stale wasm lingers in the dir that the manifest never saw."""
    d = _wasm_dir(tmp_path, {"a_parser.wasm": b"aaaa"})
    manifest = wasm_provenance.generate_manifest(d)
    (d / "py_semantic_diff_stale.wasm").write_bytes(b"stale")

    with pytest.raises(wasm_provenance.ProvenanceError, match="stale/extra"):
        wasm_provenance.verify_manifest(d, manifest)
    assert wasm_provenance.diff_manifest(d, manifest).stale == ("py_semantic_diff_stale.wasm",)


def test_verify_rejects_a_missing_artifact(tmp_path: Path) -> None:
    d = _wasm_dir(tmp_path, {"a_parser.wasm": b"aaaa", "b_parser.wasm": b"bbbb"})
    manifest = wasm_provenance.generate_manifest(d)
    (d / "b_parser.wasm").unlink()

    with pytest.raises(wasm_provenance.ProvenanceError, match="missing"):
        wasm_provenance.verify_manifest(d, manifest)


def test_verify_rejects_tampered_bytes(tmp_path: Path) -> None:
    d = _wasm_dir(tmp_path, {"a_parser.wasm": b"original"})
    manifest = wasm_provenance.generate_manifest(d)
    (d / "a_parser.wasm").write_bytes(b"tampered!")  # same name, different content

    with pytest.raises(wasm_provenance.ProvenanceError, match="mismatch"):
        wasm_provenance.verify_manifest(d, manifest)
    assert wasm_provenance.diff_manifest(d, manifest).mismatched == ("a_parser.wasm",)


def test_verify_rejects_unknown_schema_version(tmp_path: Path) -> None:
    d = _wasm_dir(tmp_path, {"a_parser.wasm": b"aaaa"})
    manifest = wasm_provenance.generate_manifest(d)
    manifest["schema_version"] = 999

    with pytest.raises(wasm_provenance.ProvenanceError, match="schema_version"):
        wasm_provenance.verify_manifest(d, manifest)


def test_manifest_is_deterministic_and_json_serialisable(tmp_path: Path) -> None:
    d = _wasm_dir(tmp_path, {"z_parser.wasm": b"zz", "a_parser.wasm": b"aa"})
    first = wasm_provenance.generate_manifest(d, built_from_commit="c")
    second = wasm_provenance.generate_manifest(d, built_from_commit="c")
    assert first == second
    # Round-trips through JSON (it ships in the wheel).
    assert json.loads(json.dumps(first)) == first
