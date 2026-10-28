"""Release provisioning must attest registry-approved bytes before writing a manifest.

The tests use synthetic component bytes and independent SHA-256 pins. The
manifest generator is not itself the trust anchor: arbitrary staged files
cannot be made trusted merely by recording their own hashes.
"""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "provision_build_inputs.py"


def _write(working: Path, components: dict[str, bytes]) -> dict[str, str]:
    working.mkdir(exist_ok=True)
    pins = {}
    for name, payload in components.items():
        (working / name).write_bytes(payload)
        pins[name] = hashlib.sha256(payload).hexdigest()
    return pins


def _provision():
    return runpy.run_path(str(SCRIPT))["write_reviewed_wasm_manifest"]


def test_manifest_requires_independently_reviewed_parser_and_renderer_pins(tmp_path: Path):
    parser = _write(tmp_path, {"python_parser.wasm": b"parser"})
    renderer = _write(tmp_path, {"html_renderer.wasm": b"renderer"})
    path = _provision()(tmp_path, parser, renderer)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert set(manifest["artifacts"]) == set(parser) | set(renderer)
    assert manifest["artifacts"]["python_parser.wasm"]["sha256"] == parser["python_parser.wasm"]
    assert manifest["artifacts"]["html_renderer.wasm"]["sha256"] == renderer["html_renderer.wasm"]
    assert manifest["built_from_commit"]  # registry reference is immutable


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("extra", "extra=\\['not_in_registry.wasm'\\]"),
        ("missing", "missing=\\['python_parser.wasm'\\]"),
        ("tampered", "does not match reviewed registry SHA-256"),
        ("overlap", "pins overlap"),
        ("no_pins", "no immutable Wasm pins"),
    ],
)
def test_manifest_cannot_attest_unreviewed_or_incomplete_staging(
    tmp_path: Path, defect: str, message: str
):
    parser = _write(tmp_path, {"python_parser.wasm": b"parser"})
    renderer = _write(tmp_path, {"html_renderer.wasm": b"renderer"})
    if defect == "extra":
        (tmp_path / "not_in_registry.wasm").write_bytes(b"unapproved")
    elif defect == "missing":
        (tmp_path / "python_parser.wasm").unlink()
    elif defect == "tampered":
        (tmp_path / "python_parser.wasm").write_bytes(b"replaced after registry approval")
    elif defect == "overlap":
        renderer["python_parser.wasm"] = parser["python_parser.wasm"]
    elif defect == "no_pins":
        parser.clear()
        renderer.clear()

    with pytest.raises(ValueError, match=message):
        _provision()(tmp_path, parser, renderer)
    assert not (tmp_path / "wasm_provenance.json").exists()
