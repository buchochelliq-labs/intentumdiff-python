#!/usr/bin/env python3
"""Fail-closed promotion of *previously tested*, immutable GitHub CI wheels.

No wheel is built, retagged, modified, or silently omitted by this program.
Run metadata is independently checked against the currently checked-out source
tree and its four successful platform jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

PLATFORMS = {
    "ubuntu-latest": r"manylinux[^/]*x86_64",
    "macos-latest": r"macosx_",
    "windows-latest": r"win_amd64",
    "windows-11-arm": r"win_arm64",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RELEASE_VERSION = re.compile(r"^(\d+\.\d+\.\d+)(?:(?:a|b|rc)\d+)?$")


def project_version() -> str:
    with Path("pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def release_branch(version: str) -> str:
    """The RC branch a version's candidate must be tested on: 0.0.3b1 -> release/v0.0.3-rc."""
    match = RELEASE_VERSION.fullmatch(version)
    require(match is not None, f"unsupported release version {version!r}")
    return f"release/v{match.group(1)}-rc"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def github_api(repo: str, path: str) -> dict:
    completed = subprocess.run(
        ["gh", "api", f"repos/{repo}/{path}"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def load_approval(env: dict[str, str]) -> tuple[str, int, str, str, dict]:
    repo = env.get("GITHUB_REPOSITORY", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None,
            "GITHUB_REPOSITORY required")
    run_raw = env.get("REVIEWED_RUN_ID", "")
    require(run_raw.isascii() and run_raw.isdecimal() and int(run_raw) > 0,
            "approved CI run ID required")
    source = env.get("REVIEWED_SOURCE_SHA", "")
    core = env.get("REVIEWED_CORE_SHA", "")
    require(bool(HEX40.fullmatch(source)), "reviewed source SHA required")
    require(bool(HEX40.fullmatch(core)), "reviewed Core SHA required")
    raw = env.get("REVIEWED_WHEELS_JSON", "")
    require(bool(raw.strip()),
            "four independently approved artifact IDs and wheel SHA-256 values required")
    try:
        specs = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "REVIEWED_WHEELS_JSON is not valid JSON (it must keep its double quotes); "
            f"received {raw[:40]!r}..."
        ) from exc
    require(isinstance(specs, dict) and set(specs) == set(PLATFORMS),
            "exactly four CI platform approvals required")
    seen_ids: set[int] = set()
    for os_name, entry in specs.items():
        require(isinstance(entry, dict) and set(entry) == {"id", "sha256"},
                f"{os_name}: require only id and sha256")
        artifact_id, sha = entry["id"], entry["sha256"]
        require(type(artifact_id) is int and artifact_id > 0 and artifact_id not in seen_ids,
                f"{os_name}: unique positive artifact ID required")
        require(isinstance(sha, str) and bool(HEX64.fullmatch(sha)),
                f"{os_name}: lowercase SHA-256 required")
        seen_ids.add(artifact_id)
    return repo, int(run_raw), source, core, specs


def verify_metadata(repo: str, run_id: int, source: str, specs: dict, version: str) -> int:
    run = github_api(repo, f"actions/runs/{run_id}")
    require(run.get("name") == "CI", "candidate must come from CI")
    require(run.get("event") == "push", "candidate must be tested on a release branch push")
    expected_branch = release_branch(version)
    require(run.get("head_branch") == expected_branch,
            f"candidate was not tested on {expected_branch}")
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "candidate CI is not successful")
    require(run.get("head_sha") == source, "candidate CI source SHA mismatch")
    require(int(run.get("run_attempt", 0)) > 0, "candidate run attempt missing")
    tree = github_api(repo, f"git/commits/{source}")["tree"]["sha"]
    checked_out_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    require(tree == checked_out_tree,
            "tag/dispatch checkout tree differs from reviewed CI source")
    jobs = github_api(repo, f"actions/runs/{run_id}/jobs?per_page=100")
    names = {j.get("name"): j for j in jobs.get("jobs", [])}
    for platform in PLATFORMS:
        name = f"Python full suite ({platform})"
        job = names.get(name)
        require(job is not None and job.get("conclusion") == "success"
                and job.get("head_sha") in (None, source),
                f"{platform}: full platform CI not passed")
    require(names.get("Pins and links we ship", {}).get("conclusion") == "success",
            "pins CI gate not passed")
    for platform, spec in specs.items():
        art = github_api(repo, f"actions/artifacts/{spec['id']}")
        expected = f"candidate-evidence-{platform}-{run['run_attempt']}"
        require(art.get("name") == expected, f"{platform}: artifact name mismatch")
        require(art.get("expired") is False, f"{platform}: candidate artifact expired")
        origin = art.get("workflow_run") or {}
        require(origin.get("id") == run_id and origin.get("head_sha") == source,
                f"{platform}: candidate artifact run/commit mismatch")
    return int(run["run_attempt"])


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_downloads(root: Path, output: Path, source: str, core: str,
                     run_attempt: int, specs: dict, version: str) -> list[str]:
    require(not output.exists() or not any(output.iterdir()),
            "promotion output must be initially empty")
    output.mkdir(parents=True, exist_ok=True)
    records: list[str] = []
    filenames: set[str] = set()
    for platform, spec in specs.items():
        artifact = root / f"candidate-evidence-{platform}-{run_attempt}"
        require(artifact.is_dir() and not artifact.is_symlink(),
                f"{platform}: approved extracted artifact missing")
        evidence = artifact / "evidence"
        dist = artifact / "dist"
        require(evidence.is_dir() and dist.is_dir(), f"{platform}: evidence/dist missing")
        require((evidence / "python-tested-commit.txt").read_text().strip() == source,
                f"{platform}: tested Python source mismatch")
        require((evidence / "core-commit.txt").read_text().strip() == core,
                f"{platform}: tested Core source mismatch")
        require((evidence / "wheel-build-profile.txt").read_text().strip() == "release",
                f"{platform}: not a release-profile wheel")
        require((evidence / "wasm_provenance.json").is_file(),
                f"{platform}: Wasm provenance missing")
        require((evidence / "migration-parity.json").is_file(),
                f"{platform}: migration acceptance evidence missing")
        wheels = sorted(dist.glob("*.whl"))
        require(len(wheels) == 1 and wheels[0].is_file() and not wheels[0].is_symlink(),
                f"{platform}: expected exactly one regular wheel")
        wheel = wheels[0]
        require(not any(p.is_symlink() for p in artifact.rglob("*")),
                f"{platform}: symlink in candidate evidence")
        require(wheel.name.startswith(f"intentumdiff_python-{version}-"),
                f"{platform}: wrong distribution/version")
        require(re.search(PLATFORMS[platform], wheel.name) is not None,
                f"{platform}: platform tag is not publishable or does not match runner")
        require(wheel.name not in filenames, "duplicate wheel filename")
        filenames.add(wheel.name)
        actual = _digest(wheel)
        require(actual == spec["sha256"], f"{platform}: reviewed SHA-256 mismatch")
        manifest = (evidence / "wheels.sha256").read_text(encoding="utf-8").splitlines()
        entries = [line.strip().split(None, 1) for line in manifest
                   if line.strip() and not line.lstrip().startswith("#")]
        require(entries == [[actual, f"dist/{wheel.name}"]],
                f"{platform}: CI checksum manifest mismatches reviewed wheel")
        dest = output / wheel.name
        with wheel.open("rb") as src, dest.open("xb") as target:
            shutil.copyfileobj(src, target)
        require(_digest(dest) == actual, f"{platform}: copied wheel changed")
        records.append(f"{actual}  {output.name}/{wheel.name}")
    (output.parent / "reviewed-wheels.sha256").write_text("\n".join(records) + "\n", encoding="utf-8")
    return records


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("metadata", "wheels"))
    p.add_argument("--download-root", type=Path, default=Path("reviewed-dist"))
    p.add_argument("--output", type=Path, default=Path("publish-dist"))
    args = p.parse_args()
    try:
        repo, run_id, source, core, specs = load_approval(os.environ)
        version = project_version()
        attempt = verify_metadata(repo, run_id, source, specs, version)
        if args.phase == "metadata":
            ids = ",".join(str(specs[name]["id"]) for name in PLATFORMS)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as f:
                f.write(f"artifact_ids={ids}\n")
            print(f"Reviewed CI run {run_id} and all four exact artifact identities verified")
        else:
            records = verify_downloads(args.download_root, args.output, source, core,
                                       attempt, specs, version)
            print(f"Promoted {len(records)} already-tested wheel bytes without rebuilding")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"::error::reviewed wheel promotion failed closed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
