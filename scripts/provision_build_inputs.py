"""Stage the wheel's build inputs — the engine checkout + the parser components.

The thin Python binding (#82 split) builds its wheel against:
  1. build/intentdiff-core — a checkout of the engine repo at CORE_REF (the
     [tool.maturin] manifest-path points into it), and
  2. src/intentdiff/wasm/*.wasm — the built parser/renderer components the wheel
     bundles (built by the parser repos / intentdiff-core; the registry pins their
     checksums).

Sources (first match wins):
  --core-dir / INTENTDIFF_CORE_DIR      an existing local checkout (copied, not cloned)
  otherwise                              `git clone --depth 1 --branch CORE_REF` of the repo
  --wasm-dir / INTENTDIFF_WASM_DIR      a dir of built .wasm components to stage

Usage:
  python scripts/provision_build_inputs.py [--core-dir PATH] [--wasm-dir PATH]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_REPO = "https://github.com/buchochelliq-labs/intentdiff-core"
CORE_REF = "main"  # pin to a tag once intentdiff-core cuts releases
CORE_DEST = REPO_ROOT / "build" / "intentdiff-core"
WASM_DEST = REPO_ROOT / "src" / "intentdiff" / "wasm"


def stage_core(core_dir: str | None) -> None:
    if CORE_DEST.exists():
        shutil.rmtree(CORE_DEST)
    CORE_DEST.parent.mkdir(parents=True, exist_ok=True)
    src = core_dir or os.environ.get("INTENTDIFF_CORE_DIR")
    if src:
        print(f"staging engine from local checkout: {src}")
        shutil.copytree(src, CORE_DEST, ignore=shutil.ignore_patterns("target", ".git"))
    else:
        print(f"cloning {CORE_REPO}@{CORE_REF}")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", CORE_REF, CORE_REPO, str(CORE_DEST)],
            check=True,
        )
    manifest = CORE_DEST / "crates" / "rust-core-host" / "Cargo.toml"
    if not manifest.exists():
        sys.exit(f"engine manifest missing after staging: {manifest}")
    print(f"engine staged: {manifest}")


def stage_wasm(wasm_dir: str | None) -> None:
    src = wasm_dir or os.environ.get("INTENTDIFF_WASM_DIR")
    if not src:
        print("NOTE: no --wasm-dir/INTENTDIFF_WASM_DIR — skipping component staging "
              "(the wheel will not bundle parser components)")
        return
    WASM_DEST.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in Path(src).glob("*.wasm"):
        shutil.copy2(item, WASM_DEST / item.name)
        count += 1
    manifest = Path(src) / "wasm_provenance.json"
    if manifest.exists():
        shutil.copy2(manifest, WASM_DEST / manifest.name)
    print(f"staged {count} components into {WASM_DEST}")


# ── Parser components from sibling-repo CI artifacts (Phase D) ────────────────
# The 69 intentdiff-<lang>-parser repos each publish their built component as a
# `parser-wasm` artifact on every successful CI run. Without those components the
# engine resolves every language to 'unknown', so the suite cannot run. This mode
# pulls the latest successful artifact from each parser repo — the embryo of the
# registry-pinned artifact flow (pinning by checksum lands with the registry wiring).

def stage_wasm_from_artifacts(token: str, org: str = "buchochelliq-labs") -> int:
    import io
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request
    import zipfile

    api = "https://api.github.com"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "User-Agent": "intentdiff-provision"}

    class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
        """Drop the Authorization header when a redirect leaves the API host.

        `artifacts/<id>/zip` 302s to Azure blob storage, which signs the request in the
        URL and REJECTS a bearer token it did not issue (401 locally, 403 on a runner).
        urllib re-sends every header across a redirect by default, so the naive fetch
        fails for a reason that reads like a permissions problem and isn't one — it
        staged 0 of 69 components and the suite then failed 594 tests on
        PluginNotFoundError('unknown').
        """

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            new = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new is not None and (
                urllib.parse.urlsplit(newurl).netloc
                != urllib.parse.urlsplit(req.full_url).netloc
            ):
                new.remove_header("Authorization")
            return new

    opener = urllib.request.build_opener(_StripAuthOnRedirect)

    def get(url: str) -> bytes:
        req = urllib.request.Request(url, headers=hdr)
        with opener.open(req, timeout=120) as r:
            return r.read()

    repos, page = [], 1
    while True:
        batch = _json.loads(get(f"{api}/orgs/{org}/repos?per_page=100&page={page}"))
        if not batch:
            break
        repos += [r["name"] for r in batch if r["name"].endswith("-parser")]
        page += 1
    print(f"parser repos: {len(repos)}")

    WASM_DEST.mkdir(parents=True, exist_ok=True)
    staged, missing = 0, []
    for name in sorted(repos):
        # Track which call failed: "HTTP 403" alone cannot distinguish a token missing
        # the actions:read scope (list-runs) from blob storage rejecting a forwarded
        # credential (download) — different fixes, one symptom.
        stage = "list-runs"
        try:
            runs = _json.loads(get(
                f"{api}/repos/{org}/{name}/actions/runs?status=success&per_page=1"))
            wr = runs.get("workflow_runs") or []
            if not wr:
                missing.append((name, "no successful run")); continue
            stage = "list-artifacts"
            arts = _json.loads(get(wr[0]["artifacts_url"]))
            art = next((a for a in arts.get("artifacts", []) if a["name"] == "parser-wasm"), None)
            if not art:
                missing.append((name, "no parser-wasm artifact")); continue
            stage = "download-artifact"
            blob = get(art["archive_download_url"])
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for member in z.namelist():
                    if member.endswith(".wasm"):
                        # strip the intentdiff_ prefix: the host stages <lang>_parser.wasm
                        out = Path(member).name
                        out = out[len("intentdiff_"):] if out.startswith("intentdiff_") else out
                        (WASM_DEST / out).write_bytes(z.read(member))
                        staged += 1
        except urllib.error.HTTPError as exc:
            missing.append((name, f"HTTP {exc.code} at {stage} ({exc.reason})"))
    print(f"staged {staged} components into {WASM_DEST}")
    if missing:
        print(f"MISSING ({len(missing)}):")
        for m in missing:
            print("  ", m)
    return len(missing)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core-dir")
    ap.add_argument("--wasm-dir")
    ap.add_argument("--from-parser-artifacts", action="store_true",
                    help="stage components from the parser repos' latest CI artifacts "
                         "(needs GH_TOKEN / GITHUB_TOKEN with read access)")
    args = ap.parse_args()
    stage_core(args.core_dir)
    if args.from_parser_artifacts:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not token:
            sys.exit("--from-parser-artifacts needs GH_TOKEN or GITHUB_TOKEN")
        missing = stage_wasm_from_artifacts(token)
        if missing:
            # Fail closed. A partial (or empty) component staging does not surface as a
            # provisioning error later — it surfaces as hundreds of
            # PluginNotFoundError('unknown') test failures an hour into the suite, which
            # reads like an engine regression. Stop here, where the cause is legible.
            sys.exit(f"provisioning FAILED: {missing} parser repo(s) had no usable "
                     f"component artifact (see MISSING above)")
    else:
        stage_wasm(args.wasm_dir)
    print("build inputs ready: `maturin build --release -b cffi` (or pip install -e .)")


if __name__ == "__main__":
    main()
