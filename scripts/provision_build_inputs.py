"""Stage the wheel's build inputs — the engine checkout + the parser components.

The thin Python binding (#82 split) builds its wheel against:
  1. build/intentumdiff-core — a checkout of the engine repo at CORE_REF (the
     [tool.maturin] manifest-path points into it), and
  2. src/intentumdiff/wasm/*.wasm — the built parser/renderer components the wheel
     bundles (built by the parser repos / intentumdiff-core; the registry pins their
     checksums).

Sources (first match wins):
  --core-dir / INTENTUMDIFF_CORE_DIR      an existing local checkout (copied, not cloned)
  otherwise                              fetch CORE_REF (branch, tag or immutable SHA)
  --wasm-dir / INTENTUMDIFF_WASM_DIR      a dir of built .wasm components to stage

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
CORE_REPO = "https://github.com/buchochelliq-labs/intentumdiff-core"
# Immutable reviewed engine candidate for this binding. Override for an explicit
# integration build; never let a moving branch silently change the engine under CI.
CORE_REF = os.environ.get("INTENTUMDIFF_CORE_REF", "35b6a522185bffe9e9c654ff183e68af4d1dba22")
CORE_DEST = REPO_ROOT / "build" / "intentumdiff-core"
WASM_DEST = REPO_ROOT / "src" / "intentumdiff" / "wasm"


def stage_core(core_dir: str | None) -> None:
    if CORE_DEST.exists():
        shutil.rmtree(CORE_DEST)
    CORE_DEST.parent.mkdir(parents=True, exist_ok=True)
    src = core_dir or os.environ.get("INTENTUMDIFF_CORE_DIR")
    if src:
        print(f"staging engine from local checkout: {src}")
        shutil.copytree(src, CORE_DEST, ignore=shutil.ignore_patterns("target", ".git"))
    else:
        print(f"fetching {CORE_REPO}@{CORE_REF}")
        subprocess.run(["git", "init", str(CORE_DEST)], check=True)
        subprocess.run(["git", "-C", str(CORE_DEST), "remote", "add", "origin", CORE_REPO], check=True)
        subprocess.run(["git", "-C", str(CORE_DEST), "fetch", "--depth", "1", "origin", CORE_REF], check=True)
        subprocess.run(["git", "-C", str(CORE_DEST), "checkout", "--detach", "FETCH_HEAD"], check=True)
    manifest = CORE_DEST / "crates" / "rust-core-host" / "Cargo.toml"
    if not manifest.exists():
        sys.exit(f"engine manifest missing after staging: {manifest}")
    print(f"engine staged: {manifest}")


def stage_wasm(wasm_dir: str | None) -> None:
    src = wasm_dir or os.environ.get("INTENTUMDIFF_WASM_DIR")
    if not src:
        print("NOTE: no --wasm-dir/INTENTUMDIFF_WASM_DIR — skipping component staging "
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
# The 69 intentumdiff-<lang>-parser repos each publish their built component as a
# `parser-wasm` artifact on every successful CI run. Without those components the
# engine resolves every language to 'unknown', so the suite cannot run. This mode
# pulls the latest successful artifact from each parser repo — the embryo of the
# registry-pinned artifact flow (pinning by checksum lands with the registry wiring).

def stage_wasm_from_artifacts(token: str, org: str = "buchochelliq-labs") -> int:
    import hashlib as _hashlib
    import io
    import http.client
    import json as _json
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    import zipfile

    api = "https://api.github.com"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "User-Agent": "intentumdiff-provision"}

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

    def get(url: str, accept: str = "application/vnd.github+json") -> bytes:
        """GET with bounded retries on TRANSIENT failures only.

        Provisioning makes many API calls, and one dropped connection used to fail the job:

            http.client.RemoteDisconnected: Remote end closed connection without response

        That is not a defect in anything we control - it is the other end hanging up. It
        became visible when CI widened from one platform to four (#9): four legs provisioning
        concurrently meet it four times as often. Left alone the matrix is permanently flaky,
        and a flaky gate is one people learn to re-run rather than read.

        Deliberately NOT retried: 401, 403, 404. A missing artifact or a rejected token is a
        real answer, and retrying it only turns a clear failure into a slow one.
        """
        req = urllib.request.Request(url, headers={**hdr, "Accept": accept})
        attempts = 4
        for attempt in range(1, attempts + 1):
            try:
                with opener.open(req, timeout=120) as r:
                    return r.read()
            except urllib.error.HTTPError as exc:
                # 5xx and 429 are the server asking us to come back. Everything else is an answer.
                if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts:
                    raise
                reason = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
                if attempt == attempts:
                    raise
                reason = type(exc).__name__
            delay = 2 ** (attempt - 1)
            print(f"  transient {reason} - retry {attempt}/{attempts - 1} in {delay}s")
            time.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    # The registry (#95) is the root of trust: it pins every official component by
    # SHA-256. Verifying the downloaded artifact against that pin is what makes this a
    # supply-chain control rather than "whatever the parser repo last built". Builds are
    # reproducible, so a mismatch means the component genuinely changed and the fix is a
    # registry PR through the vet gate — never a bypass here.
    import yaml  # pyyaml is already a runtime dep (hub.py)

    registry = yaml.safe_load(
        get(f"{api}/repos/{org}/intentumdiff-registry/contents/registry.yaml",
            accept="application/vnd.github.raw").decode("utf-8")
    )
    pins: dict[str, str] = {}
    refs: dict[str, str] = {}
    for plugin, entry in (registry.get("plugins") or {}).items():
        pins.update(entry.get("wasm_checksums") or {})
        if entry.get("ref"):
            refs[plugin] = entry["ref"]
    if not pins:
        sys.exit("registry.yaml carries no wasm_checksums - refusing to 'verify' nothing")
    print(f"registry pins: {len(pins)} component checksums, {len(refs)} refs")

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
            # The registry pins BOTH a ref and a checksum. Asking for "the newest
            # successful run" silently ignores half of that: any unrelated commit on a
            # parser repo — a CI tweak, a dependabot.yml, a README badge — repoints this
            # at a different build, and the checksum gate then fires for the wrong
            # reason, reporting "the component changed" when only the commit did.
            ref = refs.get(name)
            query = (f"head_sha={ref}&status=success&per_page=20" if ref
                     else "status=success&per_page=1")
            runs = _json.loads(get(f"{api}/repos/{org}/{name}/actions/runs?{query}"))
            wr = runs.get("workflow_runs") or []
            if not wr:
                missing.append((name, f"no successful run at pinned ref {ref[:8]}" if ref
                                else "no successful run")); continue

            # Several workflows run on one commit (CI, CodeQL, Dependabot); only one
            # publishes parser-wasm, so walk them rather than assuming the first.
            stage = "list-artifacts"
            art = None
            for run in wr:
                arts = _json.loads(get(run["artifacts_url"]))
                art = next((a for a in arts.get("artifacts", [])
                            if a["name"] == "parser-wasm" and not a.get("expired")), None)
                if art:
                    break
            if not art:
                missing.append((name, "no parser-wasm artifact")); continue
            stage = "download-artifact"
            blob = get(art["archive_download_url"])
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for member in z.namelist():
                    if member.endswith(".wasm"):
                        # Strip the crate prefix: the host stages <lang>_parser.wasm and
                        # that unprefixed form is the registry key. BOTH spellings must be
                        # accepted. The registry pins immutable artifacts BY REF, and the
                        # ones pinned today were built before the IntentDiff ->
                        # IntentumDiff rename, so they are still named intentdiff_*.wasm.
                        # Accepting only the new prefix leaves the name prefixed, and every
                        # component then reports "is not pinned in the registry".
                        out = Path(member).name
                        for _prefix in ("intentumdiff_", "intentdiff_"):
                            if out.startswith(_prefix):
                                out = out[len(_prefix):]
                                break
                        payload = z.read(member)
                        digest = _hashlib.sha256(payload).hexdigest()
                        pinned = pins.get(out)
                        # Write only what the registry vouches for — an unverified
                        # component must never reach the staging dir.
                        if pinned is None:
                            missing.append((name, f"{out} is not pinned in the registry"))
                            continue
                        if pinned != digest:
                            missing.append((name, f"{out} CHECKSUM MISMATCH (registry pins "
                                                  f"{pinned[:16]}..., artifact is {digest[:16]}...)"))
                            continue
                        (WASM_DEST / out).write_bytes(payload)
                        staged += 1
        except urllib.error.HTTPError as exc:
            missing.append((name, f"HTTP {exc.code} at {stage} ({exc.reason})"))
    # ---- renderers -------------------------------------------------------------------
    # Renderers are NOT parser repos - they are crates in intentumdiff-core, published as
    # the `renderer-components` artifact since core#26. Nothing fetched them before, so the
    # suite ran with ZERO renderers loaded and still reported green: every test touching
    # --format html|patch|llm|terminal was skipping, hitting a Python fallback, or asserting
    # on degraded output, with nothing distinguishing those from real coverage (#22).
    stage = "renderers"
    try:
        runs = _json.loads(
            get(f"{api}/repos/{org}/intentumdiff-core/actions/runs"
                f"?status=success&per_page=20")
        ).get("workflow_runs", [])
        art = None
        for run in runs:
            arts = _json.loads(get(run["artifacts_url"]))
            art = next((a for a in arts.get("artifacts", [])
                        if a["name"] == "renderer-components" and not a.get("expired")), None)
            if art:
                break
        if art is None:
            missing.append(("intentumdiff-core", "no renderer-components artifact"))
        else:
            blob = get(art["archive_download_url"])
            found = 0
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for member in z.namelist():
                    if not member.endswith(".wasm"):
                        continue
                    out = Path(member).name
                    for _prefix in ("intentumdiff_", "intentdiff_"):
                        if out.startswith(_prefix):
                            out = out[len(_prefix):]
                            break
                    payload = z.read(member)
                    digest = _hashlib.sha256(payload).hexdigest()
                    pinned = pins.get(out)
                    # Renderers are not all pinned in the registry today. Where a pin EXISTS
                    # it is enforced exactly as for parsers; where it does not, the component
                    # is staged and the fact is printed rather than silently accepted, so an
                    # unpinned component is visible instead of indistinguishable from a
                    # verified one.
                    if pinned is not None and pinned != digest:
                        missing.append(("intentumdiff-core",
                                        f"{out} CHECKSUM MISMATCH (registry pins "
                                        f"{pinned[:16]}..., artifact is {digest[:16]}...)"))
                        continue
                    if pinned is None:
                        print(f"  note: {out} is not pinned in the registry (staged unverified)")
                    (WASM_DEST / out).write_bytes(payload)
                    staged += 1
                    found += 1
            # Fail closed on a partial set. A consumer that stages 3 of 4 renderers loses one
            # silently and reports green - the exact failure this exists to end.
            _EXPECTED_RENDERERS = 4
            if found < _EXPECTED_RENDERERS:
                missing.append(("intentumdiff-core",
                                f"only {found}/{_EXPECTED_RENDERERS} renderer components in "
                                f"the artifact"))
    except urllib.error.HTTPError as exc:
        missing.append(("intentumdiff-core", f"HTTP {exc.code} at {stage} ({exc.reason})"))

    # Regenerate the provenance manifest for what was ACTUALLY staged.
    #
    # `stage_wasm()` copies a manifest if one happens to sit beside the source components;
    # this artifact path had none, so after an artifact-based staging the manifest was stale
    # or absent and the loader's provenance check either compared against the previous
    # build's hashes or could not run at all. That check is the last line between a
    # substituted component and the engine, so leaving it toothless in the path CI actually
    # uses defeats it.
    #
    # The manifest is a per-build artifact and gitignored, so it is generated here rather
    # than committed anywhere.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from wasm_provenance import MANIFEST_FILENAME, generate_manifest, write_manifest

        write_manifest(generate_manifest(WASM_DEST), WASM_DEST / MANIFEST_FILENAME)
        print(f"wrote {MANIFEST_FILENAME} for {staged} staged component(s)")
    except Exception as exc:  # noqa: BLE001
        # Do not fail provisioning over the manifest: the components are staged and usable,
        # and a hard failure here would block a build for a diagnostic aid. Say so loudly
        # instead - a silent absence is what made this worth fixing.
        print(f"WARNING: could not write {MANIFEST_FILENAME}: {type(exc).__name__}: {exc}")

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
