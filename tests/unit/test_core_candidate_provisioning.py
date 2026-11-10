"""An exact engine SHA, not a moving branch, must be provisionable for CI."""
import runpy
import subprocess
import json
import hashlib
from pathlib import Path
import pytest


def test_stage_core_accepts_immutable_commit(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts" / "provision_build_inputs.py"
    stage = runpy.run_path(str(script))["stage_core"]
    source = tmp_path / "source"
    source.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()
    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    manifest = source / "crates/rust-core-host/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("candidate one")
    git("add", ".")
    git("commit", "-m", "first candidate")
    sha = git("rev-parse", "HEAD")
    manifest.write_text("candidate two")
    git("commit", "-am", "later candidate")
    destination = tmp_path / "staged"
    monkeypatch.delenv("INTENTUMDIFF_CORE_DIR", raising=False)
    monkeypatch.setitem(stage.__globals__, "CORE_REPO", str(source))
    monkeypatch.setitem(stage.__globals__, "CORE_REF", sha)
    monkeypatch.setitem(stage.__globals__, "CORE_DEST", destination)
    stage(None)
    assert (destination / "crates/rust-core-host/Cargo.toml").read_text() == "candidate one"
    assert subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip() == sha


def test_registry_uses_reviewed_immutable_commit():
    script = Path(__file__).resolve().parents[2] / "scripts" / "provision_build_inputs.py"
    namespace = runpy.run_path(str(script))
    expected = "8911328ede24630ad9ea6a33da8ed010f066d3b1"  # Registry #17 verified character-literal candidate
    seen = []
    def get(url, **kwargs):
        seen.append(url)
        assert url.endswith("/registry.yaml?ref=" + expected)
        assert kwargs["accept"] == "application/vnd.github.raw"
        return b"plugins: {}"
    assert namespace["load_reviewed_registry"](get, "https://api.github.com", "buchochelliq-labs") == {"plugins": {}}
    assert len(seen) == 1


def test_mirror_component_accepts_only_exact_registry_source_and_checksum(tmp_path):
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "provision_build_inputs.py"))
    verify = namespace["verified_mirror_component"]
    slug = "python"
    name = "python_parser.wasm"
    ref = "a" * 40
    payload = b"reviewed parser wasm"
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / name).write_bytes(payload)
    (tmp_path / "wasm_provenance.json").write_text(json.dumps({"artifacts": {
        name: {"sha256": digest, "source": {
            "commit": ref,
            "repository": "https://github.com/buchochelliq-labs/intentumdiff-python-parser",
        }}
    }}))

    checked = verify(slug, tmp_path, ref, digest)
    assert checked[:4] == (name, ref, digest, payload)
    assert verify(slug, tmp_path, "b" * 40, digest) is None

    with pytest.raises(RuntimeError, match="source/checksum disagrees"):
        verify(slug, tmp_path, ref, "0" * 64)
    (tmp_path / name).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify(slug, tmp_path, ref, digest)
