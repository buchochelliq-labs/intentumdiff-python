"""Release provenance controls for reviewed-wheel *promotion* (no rebuild).

These use synthetic candidate artifacts, not a repackaged production wheel.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "promote_reviewed_wheels.py"
spec = importlib.util.spec_from_file_location("promote_reviewed_wheels", SCRIPT)
assert spec and spec.loader
promote = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = promote
spec.loader.exec_module(promote)

SOURCE = "a" * 40
CORE = "b" * 40


def _specs():
    return {os_name: {"id": i + 1, "sha256": "c" * 64}
            for i, os_name in enumerate(promote.PLATFORMS)}


def test_approvals_require_four_platforms_and_digests():
    env = {"GITHUB_REPOSITORY": "org/repo", "REVIEWED_RUN_ID": "123",
           "REVIEWED_SOURCE_SHA": SOURCE, "REVIEWED_CORE_SHA": CORE,
           "REVIEWED_WHEELS_JSON": __import__("json").dumps(_specs())}
    assert promote.load_approval(env)[1] == 123
    approvals = _specs()
    del approvals["windows-11-arm"]
    env["REVIEWED_WHEELS_JSON"] = __import__("json").dumps(approvals)
    with pytest.raises(ValueError, match="four"):
        promote.load_approval(env)
    approvals = _specs()
    approvals["ubuntu-latest"]["sha256"] = "bad"
    env["REVIEWED_WHEELS_JSON"] = __import__("json").dumps(approvals)
    with pytest.raises(ValueError, match="SHA-256"):
        promote.load_approval(env)


def test_metadata_denies_untested_run_and_wrong_artifacts(monkeypatch):
    run = {"name": "CI", "event": "push", "head_branch": "release/v0.0.3-rc",
           "status": "completed", "conclusion": "success", "head_sha": SOURCE,
           "run_attempt": 1}
    jobs = {"jobs": [{"name": f"Python full suite ({os_name})", "conclusion": "success"}
                     for os_name in promote.PLATFORMS] +
                    [{"name": "Pins and links we ship", "conclusion": "success"}]}
    artifacts = {i + 1: {"name": f"candidate-evidence-{platform}-1",
                         "expired": False,
                         "workflow_run": {"id": 123, "head_sha": SOURCE}}
                 for i, platform in enumerate(promote.PLATFORMS)}
    def fake_api(repo, path):
        if path == "actions/runs/123": return run
        if path == f"git/commits/{SOURCE}": return {"tree": {"sha": "tree-id"}}
        if path == "actions/runs/123/jobs?per_page=100": return jobs
        if path.startswith("actions/artifacts/"): return artifacts[int(path.rsplit("/", 1)[1])]
        raise AssertionError(path)
    class Completed:
        stdout = "tree-id\n"
    monkeypatch.setattr(promote, "github_api", fake_api)
    monkeypatch.setattr(promote.subprocess, "run", lambda *a, **kw: Completed())
    assert promote.verify_metadata("org/repo", 123, SOURCE, _specs(), "0.0.3b1") == 1
    run["conclusion"] = "failure"
    with pytest.raises(ValueError, match="not successful"):
        promote.verify_metadata("org/repo", 123, SOURCE, _specs(), "0.0.3b1")
    run["conclusion"] = "success"
    # A 0.0.3 candidate tested on the previous release branch is not promotable.
    run["head_branch"] = "release/v0.0.2-rc"
    with pytest.raises(ValueError, match="release/v0.0.3-rc"):
        promote.verify_metadata("org/repo", 123, SOURCE, _specs(), "0.0.3b1")
    run["head_branch"] = "release/v0.0.3-rc"
    artifacts[2]["workflow_run"]["head_sha"] = "d" * 40
    with pytest.raises(ValueError, match="run/commit mismatch"):
        promote.verify_metadata("org/repo", 123, SOURCE, _specs(), "0.0.3b1")


def _candidate(root: Path, platform: str, filename: str, content=b"wheel"):
    candidate = root / f"candidate-evidence-{platform}-1"
    evidence, dist = candidate / "evidence", candidate / "dist"
    evidence.mkdir(parents=True)
    dist.mkdir()
    (dist / filename).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (evidence / "python-tested-commit.txt").write_text(SOURCE)
    (evidence / "core-commit.txt").write_text(CORE)
    (evidence / "wheel-build-profile.txt").write_text("release")
    (evidence / "wasm_provenance.json").write_text("{}")
    (evidence / "migration-parity.json").write_text("{}")
    (evidence / "wheels.sha256").write_text(f"# generated\n{digest}  dist/{filename}\n")
    return digest


def _all_candidates(root):
    filenames = {
        "ubuntu-latest": "intentumdiff_python-0.0.2-py3-none-manylinux_2_35_x86_64.whl",
        "macos-latest": "intentumdiff_python-0.0.2-py3-none-macosx_11_0_arm64.whl",
        "windows-latest": "intentumdiff_python-0.0.2-py3-none-win_amd64.whl",
        "windows-11-arm": "intentumdiff_python-0.0.2-py3-none-win_arm64.whl",
    }
    return {p: {"id": i + 1, "sha256": _candidate(root, p, name)}
            for i, (p, name) in enumerate(filenames.items())}


def test_promote_exact_previously_smoked_wheels(tmp_path):
    specs = _all_candidates(tmp_path / "download")
    out = tmp_path / "publish"
    records = promote.verify_downloads(tmp_path / "download", out, SOURCE, CORE, 1, specs, "0.0.2")
    assert len(records) == 4
    assert len(list(out.glob("*.whl"))) == 4
    assert (out.parent / "reviewed-wheels.sha256").read_text().splitlines() == records


def test_reject_unpublishable_linux_platform_even_if_hash_approved(tmp_path):
    specs = _all_candidates(tmp_path / "download")
    wheel = next((tmp_path / "download" / "candidate-evidence-ubuntu-latest-1" / "dist").glob("*.whl"))
    invalid = wheel.with_name(wheel.name.replace("manylinux_2_35_", "linux_"))
    wheel.rename(invalid)
    digest = specs["ubuntu-latest"]["sha256"]
    evidence = wheel.parent.parent / "evidence"
    (evidence / "wheels.sha256").write_text(f"{digest}  dist/{invalid.name}\n")
    with pytest.raises(ValueError, match="platform tag"):
        promote.verify_downloads(tmp_path / "download", tmp_path / "publish",
                                 SOURCE, CORE, 1, specs, "0.0.2")


def test_reject_digest_mismatch_and_core_pin_drift(tmp_path):
    specs = _all_candidates(tmp_path / "download")
    specs["windows-latest"]["sha256"] = "c" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        promote.verify_downloads(tmp_path / "download", tmp_path / "publish",
                                 SOURCE, CORE, 1, specs, "0.0.2")
    specs = _all_candidates(tmp_path / "another")
    (tmp_path / "another" / "candidate-evidence-macos-latest-1" /
     "evidence" / "core-commit.txt").write_text("d" * 40)
    with pytest.raises(ValueError, match="Core source mismatch"):
        promote.verify_downloads(tmp_path / "another", tmp_path / "publish2",
                                 SOURCE, CORE, 1, specs, "0.0.2")


@pytest.mark.parametrize("version, branch", [
    ("0.0.3", "release/v0.0.3-rc"),
    ("0.0.3b1", "release/v0.0.3-rc"),
    ("0.0.3rc2", "release/v0.0.3-rc"),
    ("1.2.10a4", "release/v1.2.10-rc"),
])
def test_release_branch_follows_the_project_version(version, branch):
    assert promote.release_branch(version) == branch


@pytest.mark.parametrize("version", ["0.0", "0.0.3.post1", "0.0.3.dev1", "v0.0.3"])
def test_release_branch_rejects_unsupported_versions(version):
    with pytest.raises(ValueError, match="unsupported release version"):
        promote.release_branch(version)


def test_unquoted_wheels_json_reports_invalid_json():
    env = {"GITHUB_REPOSITORY": "org/repo", "REVIEWED_RUN_ID": "123",
           "REVIEWED_SOURCE_SHA": SOURCE, "REVIEWED_CORE_SHA": CORE,
           "REVIEWED_WHEELS_JSON": "{ubuntu-latest:{id:1,sha256:abc}}"}
    with pytest.raises(ValueError, match="not valid JSON"):
        promote.load_approval(env)
    env["REVIEWED_WHEELS_JSON"] = ""
    with pytest.raises(ValueError, match="four independently approved"):
        promote.load_approval(env)
