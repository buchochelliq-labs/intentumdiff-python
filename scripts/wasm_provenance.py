"""Package-time Wasm artifact provenance manifest (issue #89 — supply-chain hardening).

The staged Wasm parsers in ``src/intentumdiff/wasm/`` are gitignored build output. The #87
parity/fuzz sweep proved stale artifacts linger there (30 pre-rebrand ``py_semantic_diff_*``
parsers exporting a retired WIT world). Packaging must not trust whatever happens to be staged.

This module is the enforcement primitive:

* :func:`generate_manifest` scans a wasm dir and produces ``{filename -> sha256 + size}`` (plus
  the build commit). Run at wheel-build time; the manifest ships in the wheel so downstream
  consumers can verify provenance.
* :func:`verify_manifest` asserts a staged wasm dir matches a manifest EXACTLY — no stale,
  extra, missing, or hash-mismatched artifact — i.e. the #87 sweep as a package gate. It also
  backs the loader-side "verify a first-party bundled artifact before load" path.

Deliberately dependency-free (stdlib only) so it can run inside the maturin/wheel build with no
import of the package under construction. The committed ``parser_manifest.json`` stays SHA-free
(``gen_parser_manifest.py`` — the resolution mapping derives from committed source); provenance
SHAs are a build artifact, generated here, never committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "wasm_provenance.json"
_CHUNK = 1 << 20  # 1 MiB streaming read


class ProvenanceError(ValueError):
    """A staged wasm set does not match its provenance manifest."""


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of *path* (files can be tens of MB)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _staged_wasm(wasm_dir: Path) -> dict[str, Path]:
    """Every ``*.wasm`` directly in *wasm_dir*, keyed by basename (sorted, deterministic)."""
    return {p.name: p for p in sorted(wasm_dir.glob("*.wasm"))}


def generate_manifest(wasm_dir: Path, *, built_from_commit: str | None = None) -> dict:
    """Build the provenance manifest for every ``*.wasm`` in *wasm_dir*.

    Returns a JSON-serialisable dict; the caller writes it (see :func:`write_manifest`). The
    manifest never includes itself even if a stale copy is staged in *wasm_dir*.
    """
    artifacts: dict[str, dict[str, object]] = {}
    for name, path in _staged_wasm(wasm_dir).items():
        artifacts[name] = {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "built_from_commit": built_from_commit,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


def write_manifest(manifest: dict, out_path: Path) -> None:
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class ProvenanceMismatch:
    """The precise set/hash divergence between a staged dir and its manifest."""

    stale: tuple[str, ...]  # staged but NOT in the manifest (the #87 pre-rebrand case)
    missing: tuple[str, ...]  # in the manifest but NOT staged
    mismatched: tuple[str, ...]  # staged with a different sha256 or size

    @property
    def ok(self) -> bool:
        return not (self.stale or self.missing or self.mismatched)

    def describe(self) -> str:
        parts = []
        if self.stale:
            parts.append(f"stale/extra (not in manifest): {', '.join(self.stale)}")
        if self.missing:
            parts.append(f"missing (in manifest, not staged): {', '.join(self.missing)}")
        if self.mismatched:
            parts.append(f"hash/size mismatch: {', '.join(self.mismatched)}")
        return "; ".join(parts) or "no mismatch"


def diff_manifest(wasm_dir: Path, manifest: dict) -> ProvenanceMismatch:
    """Compare the staged wasm against *manifest* WITHOUT raising."""
    staged = _staged_wasm(wasm_dir)
    expected: dict[str, dict] = manifest.get("artifacts", {})
    staged_names = set(staged)
    expected_names = set(expected)

    mismatched: list[str] = []
    for name in sorted(staged_names & expected_names):
        want = expected[name]
        if (
            sha256_file(staged[name]) != want.get("sha256")
            or staged[name].stat().st_size != want.get("size_bytes")
        ):
            mismatched.append(name)
    return ProvenanceMismatch(
        stale=tuple(sorted(staged_names - expected_names)),
        missing=tuple(sorted(expected_names - staged_names)),
        mismatched=tuple(mismatched),
    )


def verify_manifest(wasm_dir: Path, manifest: dict) -> None:
    """Raise :class:`ProvenanceError` unless *wasm_dir* matches *manifest* exactly."""
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ProvenanceError(
            f"unsupported provenance schema_version {manifest.get('schema_version')!r} "
            f"(expected {SCHEMA_VERSION})"
        )
    mismatch = diff_manifest(wasm_dir, manifest)
    if not mismatch.ok:
        raise ProvenanceError(f"wasm provenance mismatch in {wasm_dir}: {mismatch.describe()}")


def _resolve_commit() -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S603, S607 - fixed argv, git on PATH, no shell
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    default_wasm = Path(__file__).resolve().parent.parent / "src" / "intentumdiff" / "wasm"

    gen = sub.add_parser("generate", help="write the provenance manifest for a wasm dir")
    gen.add_argument("--wasm-dir", type=Path, default=default_wasm)
    gen.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"output path (default <wasm-dir>/{MANIFEST_FILENAME})",
    )

    ver = sub.add_parser("verify", help="assert a wasm dir matches a provenance manifest")
    ver.add_argument("--wasm-dir", type=Path, default=default_wasm)
    ver.add_argument("--manifest", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "generate":
        wasm_dir: Path = args.wasm_dir
        if not wasm_dir.is_dir():
            print(f"ERROR: wasm dir not found: {wasm_dir}", file=sys.stderr)
            return 1
        manifest = generate_manifest(wasm_dir, built_from_commit=_resolve_commit())
        out = args.out or (wasm_dir / MANIFEST_FILENAME)
        write_manifest(manifest, out)
        print(f"Wrote {out} — {manifest['artifact_count']} wasm artifacts.")
        return 0

    # verify
    wasm_dir = args.wasm_dir
    manifest_path = args.manifest or (wasm_dir / MANIFEST_FILENAME)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_manifest(wasm_dir, manifest)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"Verified {wasm_dir}: {manifest.get('artifact_count')} wasm artifacts "
        f"match {manifest_path.name}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
