"""An exact engine SHA, not a moving branch, must be provisionable for CI."""
import runpy
import subprocess
from pathlib import Path


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
