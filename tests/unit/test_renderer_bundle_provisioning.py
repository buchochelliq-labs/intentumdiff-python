"""Release renderer artifact stage must use exact reviewed source and payload hashes."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import runpy
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "provision_build_inputs.py"
HASHES = {name: hashlib.sha256(name.encode()).hexdigest() for name in (
    "html_renderer.wasm", "llm_renderer.wasm",
    "patch_renderer.wasm", "terminal_renderer.wasm",
)}


def _bundle(**changes):
    value = {
        "repository": "intentumdiff-core", "ref": "a" * 40,
        "artifact": "renderer-components", "wasm_checksums": dict(HASHES),
    }
    value.update(changes)
    return {"renderer_components": value}


def _zip(**overrides):
    components = {name: name.encode() for name in HASHES}
    components.update(overrides)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, payload in components.items():
            if payload is not None:
                z.writestr(name, payload)
    return out.getvalue()


def test_reviewed_renderer_bundle_requires_exact_sha_and_four_pins():
    verify = runpy.run_path(str(SCRIPT))["reviewed_renderer_bundle"]
    assert verify(_bundle()) == ("intentumdiff-core", "a" * 40, "renderer-components", HASHES)
    with pytest.raises(ValueError, match="missing"):
        verify({})
    with pytest.raises(ValueError, match="immutable"):
        verify(_bundle(ref="main"))
    with pytest.raises(ValueError, match="exactly"):
        verify(_bundle(wasm_checksums={"html_renderer.wasm": HASHES["html_renderer.wasm"]}))
    with pytest.raises(ValueError, match="SHA-256"):
        verify(_bundle(wasm_checksums={**HASHES, "html_renderer.wasm": "0"}))
    with pytest.raises(ValueError, match="reviewed core repository"):
        verify(_bundle(repository="intentumdiff-other"))


def test_renderer_archive_verifies_all_bytes_before_staging():
    verify = runpy.run_path(str(SCRIPT))["verified_renderer_archive"]
    assert verify(_zip(), HASHES) == {name: name.encode() for name in HASHES}
    with pytest.raises(ValueError, match="CHECKSUM MISMATCH"):
        verify(_zip(**{"html_renderer.wasm": b"altered"}), HASHES)
    with pytest.raises(ValueError, match="missing"):
        verify(_zip(**{"patch_renderer.wasm": None}), HASHES)
    with pytest.raises(ValueError, match="unexpected renderer"):
        verify(_zip(**{"evil_renderer.wasm": b"unexpected"}), HASHES)
    with pytest.raises(ValueError, match="unexpected renderer"):
        verify(_zip(**{"subdir/html_renderer.wasm": b"unexpected"}), HASHES)
    with pytest.raises(ValueError, match="incomplete pin inventory"):
        verify(_zip(), {"html_renderer.wasm": HASHES["html_renderer.wasm"]})


def test_duplicate_renderer_member_is_never_silently_overwritten():
    verify = runpy.run_path(str(SCRIPT))["verified_renderer_archive"]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name in HASHES:
            z.writestr(name, name.encode())
        z.writestr("html_renderer.wasm", b"altered")
    with pytest.raises(ValueError, match="duplicate"):
        verify(data.getvalue(), HASHES)
