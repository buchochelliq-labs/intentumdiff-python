#!/usr/bin/env python3
"""
scripts/verify_artifacts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Compute and verify SHA-256 checksums for built artifacts (.wasm and .whl).

Usage
-----
Record checksums (run after build, before release):

    python scripts/verify_artifacts.py record

Verify checksums (run after downloading or on CI):

    python scripts/verify_artifacts.py verify

The checksum manifest is written to / read from ``artifacts.sha256`` in the
repository root by default.  Pass ``--manifest PATH`` to override.

The script exits with a non-zero code if any checksum fails to verify.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Default paths to scan — relative to the repository root.
_DEFAULT_PATTERNS: list[str] = [
    "dist/*.whl",
    "dist/*.tar.gz",
    "src/intentdiff/**/*.wasm",
]

# Marker line in the manifest so it is clearly machine-generated.
_HEADER = "# IntentDiff artifact checksums (SHA-256)\n"


def _repo_root() -> Path:
    """Return the repository root (parent of the ``scripts/`` directory)."""
    return Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_paths(root: Path, patterns: list[str]) -> list[Path]:
    """Expand *patterns* (glob strings) relative to *root*."""
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(root.glob(pattern)))
    return paths


def cmd_record(args: argparse.Namespace) -> int:
    """Compute checksums and write them to the manifest file."""
    root = Path(args.root).resolve()
    manifest = Path(args.manifest)
    paths = _collect_paths(root, args.patterns)

    if not paths:
        print("WARNING: no artifact files found matching the given patterns.", file=sys.stderr)
        for p in args.patterns:
            print(f"  pattern: {p}", file=sys.stderr)
        return 1

    lines: list[str] = [_HEADER]
    for path in paths:
        rel = path.relative_to(root)
        digest = _sha256(path)
        lines.append(f"{digest}  {rel.as_posix()}\n")
        print(f"  recorded  {digest[:12]}…  {rel}")

    manifest.write_text("".join(lines), encoding="utf-8")
    print(f"\nManifest written to: {manifest}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify checksums from the manifest file."""
    root = Path(args.root).resolve()
    manifest = Path(args.manifest)

    if not manifest.exists():
        print(f"ERROR: manifest file not found: {manifest}", file=sys.stderr)
        return 1

    failures: list[str] = []
    ok_count = 0
    for lineno, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            print(
                f"WARNING: malformed line {lineno} in manifest, skipping: {line!r}",
                file=sys.stderr,
            )
            continue

        expected_digest, rel_path_str = parts
        path = root / rel_path_str

        if not path.exists():
            failures.append(f"  MISSING   {rel_path_str}")
            continue

        actual_digest = _sha256(path)
        if actual_digest != expected_digest:
            failures.append(
                f"  MISMATCH  {rel_path_str}\n"
                f"            expected: {expected_digest}\n"
                f"            actual:   {actual_digest}"
            )
        else:
            print(f"  OK        {actual_digest[:12]}…  {rel_path_str}")
            ok_count += 1

    if failures:
        print(f"\n{len(failures)} verification failure(s):", file=sys.stderr)
        for msg in failures:
            print(msg, file=sys.stderr)
        return 1

    print(f"\nAll {ok_count} checksum(s) verified successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record or verify SHA-256 checksums for IntentDiff artifacts."
    )
    parser.add_argument(
        "--manifest",
        default=str(_repo_root() / "artifacts.sha256"),
        metavar="PATH",
        help="Path to the checksum manifest file (default: artifacts.sha256 in repo root).",
    )
    parser.add_argument(
        "--root",
        default=str(_repo_root()),
        metavar="PATH",
        help="Root directory used to collect and verify relative artifact paths.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    record_p = sub.add_parser("record", help="Compute and record artifact checksums.")
    record_p.add_argument(
        "--patterns",
        nargs="+",
        default=_DEFAULT_PATTERNS,
        metavar="GLOB",
        help="Glob patterns (relative to repo root) to match artifact files.",
    )
    record_p.set_defaults(func=cmd_record)

    verify_p = sub.add_parser("verify", help="Verify artifact checksums against the manifest.")
    verify_p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
