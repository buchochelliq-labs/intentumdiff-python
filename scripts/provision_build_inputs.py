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
# Immutable candidate from Core #187: metered Wasmtime 48.0.7, verified
# parser availability, Rust-owned semantic promotions and fail-closed live
# diff/commit-review completeness and Rust-owned live provenance. Four-platform CI must independently pass.
# Override only for deliberate integration builds, not a moving branch.
# Core #187 now emits explicit guardrail_violations even for valid empty Rust
# batch inventories; Python #202 rejects omitted fields rather than defaulting.
CORE_REF = os.environ.get("INTENTUMDIFF_CORE_REF", "c6612f585d743bb8f6c5aa7797be44bd0263be36")
# Reviewed registry RC candidate: VB.NET plus exact-span M/Q#/PHP parser Wasm.
# Registry PR #13 CI has vetted this immutable manifest; every staged byte
# remains separately verified against its source artifact and SHA-256.
REGISTRY_REF = "e1b7c14ee03047c7c7a8ba22f8d6c31a806e65cd"


def load_reviewed_registry(get, api: str, org: str):
    import yaml
    url = f"{api}/repos/{org}/intentumdiff-registry/contents/registry.yaml?ref={REGISTRY_REF}"
    return yaml.safe_load(get(url, accept="application/vnd.github.raw").decode("utf-8"))


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



# Release bundle provenance is separate from the user-facing parser plugin
# registry. These are host packaging checks, not Python diff semantics.
REQUIRED_RENDERER_FILES = frozenset({
    "html_renderer.wasm", "llm_renderer.wasm",
    "patch_renderer.wasm", "terminal_renderer.wasm",
})


def reviewed_renderer_bundle(registry: dict) -> tuple[str, str, str, dict[str, str]]:
    """Fail closed unless all four renderers have immutable, reviewed identity."""
    bundle = registry.get("renderer_components")
    if not isinstance(bundle, dict):
        raise ValueError("registry renderer_components bundle is missing")
    if set(bundle) != {"repository", "ref", "artifact", "wasm_checksums"}:
        raise ValueError("registry renderer_components fields differ from the reviewed contract")
    repo, ref, artifact = (bundle["repository"], bundle["ref"], bundle["artifact"])
    if repo != "intentumdiff-core" or artifact != "renderer-components":
        raise ValueError("renderer artifact must come from the reviewed core repository")
    if not isinstance(ref, str) or len(ref) != 40 or any(c not in "0123456789abcdef" for c in ref):
        raise ValueError("renderer artifact source must be an immutable 40-digit git SHA")
    pins = bundle["wasm_checksums"]
    if not isinstance(pins, dict) or set(pins) != REQUIRED_RENDERER_FILES:
        raise ValueError("renderer bundle must pin exactly html, llm, patch and terminal")
    for name, digest in pins.items():
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"renderer {name} has no valid SHA-256 pin")
    return repo, ref, artifact, pins


def verified_renderer_archive(blob: bytes, pins: dict[str, str]) -> dict[str, bytes]:
    """Verify the exact CI artifact bytes before staging any renderer.

    No 'newest successful' fallback, missing checksum bypass, partial rendering
    set, unexpected Wasm file or duplicate member may enter a release wheel.
    """
    import hashlib
    import io
    import zipfile

    if set(pins) != REQUIRED_RENDERER_FILES:
        raise ValueError("renderer artifact has an incomplete pin inventory")
    checked: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.endswith(".wasm"):
                continue
            # The reviewed Core artifact publishes four flat exact names.
            # Do not normalize an ambiguous/path-traversing ZIP member into a pin.
            name = info.filename
            if "/" in name or "\\" in name or name not in REQUIRED_RENDERER_FILES:
                raise ValueError(f"unexpected renderer component {name!r}")
            if name in checked:
                raise ValueError(f"duplicate renderer component {name}")
            payload = archive.read(info)
            actual = hashlib.sha256(payload).hexdigest()
            if pins[name] != actual:
                raise ValueError(f"{name} CHECKSUM MISMATCH (registry {pins[name]}, artifact {actual})")
            checked[name] = payload
    missing = REQUIRED_RENDERER_FILES - checked.keys()
    if missing:
        raise ValueError(f"renderer-components artifact missing {sorted(missing)}")
    return checked



def write_reviewed_wasm_manifest(
    wasm_dir: Path,
    parser_pins: dict[str, str],
    renderer_pins: dict[str, str],
    sources: dict[str, dict],
) -> Path:
    """Attest registry-approved Wasm bytes AND the CI artifact that supplied each.

    A locally generated digest is never accepted as an independent source pin.
    Every staged component needs an immutable registry checksum, source commit,
    CI artifact identity and exact downloaded archive checksum before packaging.
    """
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from wasm_provenance import MANIFEST_FILENAME, generate_manifest, verify_manifest, write_manifest

    overlap = set(parser_pins) & set(renderer_pins)
    if overlap:
        raise ValueError(f"parser/renderer registry pins overlap: {sorted(overlap)}")
    expected = {**parser_pins, **renderer_pins}
    if not expected:
        raise ValueError("no immutable Wasm pins available for provenance")
    manifest = generate_manifest(wasm_dir)
    staged = manifest["artifacts"]
    extra = set(staged) - set(expected)
    missing = set(expected) - set(staged)
    if extra or missing:
        raise ValueError(
            "staged Wasm inventory differs from reviewed registry: "
            f"extra={sorted(extra)}, missing={sorted(missing)}"
        )
    if set(sources) != set(expected):
        raise ValueError(
            "component source inventory differs from reviewed registry: "
            f"extra={sorted(set(sources) - set(expected))}, "
            f"missing={sorted(set(expected) - set(sources))}"
        )
    for name, pin in expected.items():
        if staged[name]["sha256"] != pin:
            raise ValueError(f"staged Wasm {name} does not match reviewed registry SHA-256")
        source = sources[name]
        if not isinstance(source, dict) or set(source) != {
            "repository", "commit", "artifact", "artifact_id",
            "artifact_archive_sha256", "abi_target",
        }:
            raise ValueError(f"staged Wasm {name} has incomplete source attribution")
        repo, commit = source["repository"], source["commit"]
        if (not isinstance(repo, str)
                or not repo.startswith("https://github.com/buchochelliq-labs/intentumdiff-")
                or not isinstance(commit, str) or len(commit) != 40
                or any(c not in "0123456789abcdef" for c in commit)):
            raise ValueError(f"staged Wasm {name} lacks immutable source repository/commit")
        archive_sha = source["artifact_archive_sha256"]
        if (not isinstance(archive_sha, str) or len(archive_sha) != 64
                or any(c not in "0123456789abcdef" for c in archive_sha)
                or not isinstance(source["artifact_id"], int)
                or isinstance(source["artifact_id"], bool)
                or source["artifact_id"] < 1
                or source["artifact"] not in ("parser-wasm", "renderer-components")
                or (source["artifact"] == "renderer-components") != (name in renderer_pins)
                or (source["abi_target"] is not None
                    and not isinstance(source["abi_target"], str))):
            raise ValueError(f"staged Wasm {name} has invalid CI artifact attribution")
        staged[name]["source"] = dict(source)
    # This is the immutable *registry* commit, never mislabelled as a component
    # build commit; each component records its own actual source commit above.
    manifest["registry_commit"] = REGISTRY_REF
    verify_manifest(wasm_dir, manifest)
    output = wasm_dir / MANIFEST_FILENAME
    write_manifest(manifest, output)
    return output


# ── Parser components from sibling-repo CI artifacts (Phase D) ────────────────
# The 69 intentumdiff-<lang>-parser repos each publish their built component as a
# `parser-wasm` artifact on every successful CI run. Without those components the
# engine resolves every language to 'unknown', so the suite cannot run. This mode
# pulls the latest successful artifact from each parser repo — the embryo of the
# registry-pinned artifact flow (pinning by checksum lands with the registry wiring).

def _successful_artifact_runs(get, api: str, org: str, repo: str, ref: str | None = None) -> list[dict]:
    """Search bounded run history without losing pinned builds behind scheduled jobs."""
    import json
    base_query = "status=success&per_page=100"
    queries = [f"{base_query}&head_sha={ref}", base_query] if ref else [base_query]
    # A successful 200 response can temporarily omit a known pinned build from
    # both views (observed for Haskell and Make in run 37341204158). Retry the
    # exact lookup twice; never substitute another revision or bypass checksums.
    import time
    attempts = 3 if ref else 1
    for attempt in range(attempts):
        for query in queries:
            result: list[dict] = []
            for page in range(1, 11):
                runs = json.loads(get(f"{api}/repos/{org}/{repo}/actions/runs?{query}&page={page}"))
                batch = runs.get("workflow_runs") or []
                result.extend(run for run in batch
                              if (not ref or run.get("head_sha") == ref)
                              and not run.get("path", "").startswith("dynamic/"))
                if len(batch) < 100:
                    break
            if result:
                return result
            # CI observed an empty filtered lookup for a pinned Lua build that
            # remained available in repository history. Search that bounded history
            # once, still requiring the exact registry SHA above. Downloaded bytes
            # must also pass the existing checksum verification before staging.
        if attempt + 1 < attempts:
            delay = 2 ** attempt
            print(f"  no build at exact pin {ref} for {repo}; retry {attempt + 1}/{attempts - 1} in {delay}s")
            time.sleep(delay)
    return []


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
    registry = load_reviewed_registry(get, api, org)
    print(f"registry commit: {REGISTRY_REF}")
    renderer_repo, renderer_ref, renderer_artifact, renderer_pins = reviewed_renderer_bundle(registry)
    print(f"renderer bundle: {renderer_repo}@{renderer_ref} ({len(renderer_pins)} SHA-256 pins)")
    pins: dict[str, str] = {}
    refs: dict[str, str] = {}
    provenance_sources: dict[str, dict] = {}
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
            if (not isinstance(ref, str) or len(ref) != 40
                    or any(c not in "0123456789abcdef" for c in ref)):
                missing.append((name, "registry has no immutable parser source commit"))
                continue
            wr = _successful_artifact_runs(get, api, org, name, ref)
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
                        if out in provenance_sources:
                            missing.append((name, f"duplicate staged component {out}"))
                            continue
                        metadata = (registry.get("plugins") or {}).get(name, {})
                        abi_target = metadata.get("abi_target")
                        if not isinstance(abi_target, str) or not abi_target:
                            missing.append((name, f"{out} has no registry WIT/ABI target"))
                            continue
                        provenance_sources[out] = {
                            "repository": f"https://github.com/{org}/{name}",
                            "commit": ref,
                            "artifact": "parser-wasm",
                            "artifact_id": art["id"],
                            "artifact_archive_sha256": _hashlib.sha256(blob).hexdigest(),
                            "abi_target": abi_target,
                        }
                        (WASM_DEST / out).write_bytes(payload)
                        staged += 1
        except urllib.error.HTTPError as exc:
            missing.append((name, f"HTTP {exc.code} at {stage} ({exc.reason})"))
    # ---- renderers -------------------------------------------------------------------
    # A renderer bundle is an immutable core artifact, not one of the parser
    # repos. The exact core commit and *each* of its four payload digests are
    # independently reviewed in registry.yaml. Never choose the newest
    # successful core build or stage an unpinned/mismatched component.
    stage = "renderers"
    try:
        runs = _successful_artifact_runs(get, api, org, renderer_repo, renderer_ref)
        art = None
        for run in runs:
            arts = _json.loads(get(run["artifacts_url"]))
            art = next((a for a in arts.get("artifacts", [])
                        if a["name"] == renderer_artifact and not a.get("expired")), None)
            if art:
                break
        if art is None:
            missing.append((renderer_repo, f"no {renderer_artifact} artifact at reviewed ref {renderer_ref}"))
        else:
            blob = get(art["archive_download_url"])
            try:
                checked = verified_renderer_archive(blob, renderer_pins)
            except (ValueError, zipfile.BadZipFile) as exc:
                missing.append((renderer_repo, str(exc)))
            else:
                for name, payload in checked.items():
                    provenance_sources[name] = {
                        "repository": f"https://github.com/{org}/{renderer_repo}",
                        "commit": renderer_ref,
                        "artifact": renderer_artifact,
                        "artifact_id": art["id"],
                        "artifact_archive_sha256": _hashlib.sha256(blob).hexdigest(),
                        "abi_target": None,
                    }
                    (WASM_DEST / name).write_bytes(payload)
                    staged += 1
                print(f"  verified and staged {len(checked)}/{len(REQUIRED_RENDERER_FILES)} renderer components")
    except urllib.error.HTTPError as exc:
        missing.append((renderer_repo, f"HTTP {exc.code} at {stage} ({exc.reason})"))

    # Never invent source attestation by hashing whatever was staged. Verify the
    # exact reviewed 69-parser/four-renderer registry inventory before generating
    # a shipping manifest. Any provenance failure aborts provisioning.
    if missing:
        print(f"MISSING ({len(missing)}):")
        for m in missing:
            print("  ", m)
        return len(missing)
    manifest_path = write_reviewed_wasm_manifest(WASM_DEST, pins, renderer_pins, provenance_sources)
    print(f"wrote {manifest_path.name} for {staged} registry-approved component(s)")
    print(f"staged {staged} components into {WASM_DEST}")
    return 0


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
