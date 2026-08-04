"""Verify that an IntentumDiff wheel contains the native Rust core.

The first public package should be a platform wheel, not a pure-Python wheel:
users install ``intentumdiff`` once and get the Python API, built-in Wasm plugin
assets, and the native Rust core together. The core ships either as the PyO3
extension (``.pyd``/``.so``/``.dylib``) or, in the PyO3-free build, as a bare
cdylib (``.dll``/``.so``/``.dylib``) loaded via ctypes over the C ABI (#82/B.6).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath, PureWindowsPath

PROVENANCE_ENTRY = "intentumdiff/wasm/wasm_provenance.json"

# The PyO3 extension (`.pyd`/`.so`/`.dylib`) or the PyO3-free cdylib (adds Windows `.dll`; the
# Unix cdylib keeps `.so`/`.dylib`). The `unexpected_native` check below still fails on any native
# file whose name is not `intentumdiff_rust_core`, so a stray `.dll` cannot slip in.
NATIVE_SUFFIXES = (".pyd", ".so", ".dylib", ".dll")
REQUIRED_PACKAGES = {
    "intentumdiff/__init__.py",
}
NATIVE_MODULE_NAME = "intentumdiff_rust_core"
BLOCKED_ARCHIVE_BASENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "pip.conf",
    "pip.ini",
    "sitecustomize.py",
    "usercustomize.py",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
BLOCKED_ARCHIVE_SUFFIXES = (
    ".pth",
    ".pyc",
    ".pyo",
    ".exe",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".pem",
    ".key",
    ".crt",
    ".p12",
    ".pfx",
)
BYTES_PER_MB = 1_000_000
DEFAULT_MAX_WHEEL_BYTES = 75 * BYTES_PER_MB
DEFAULT_MAX_RELEASE_BYTES = 250 * BYTES_PER_MB
# The PROJECT name (what METADATA `Name:` carries and what PyPI shows). The bare
# "intentumdiff" PyPI name is held by a third party; see pyproject.toml. Wheel FILENAMES
# and the .dist-info prefix use the PEP 427 escaped form (`-` becomes `_`) — derived
# via _filename_form() wherever a path is compared, so one flag serves both roles.
EXPECTED_DISTRIBUTION = "intentumdiff-python"
EXPECTED_VERSION = "0.0.1"


def _filename_form(project_name: str) -> str:
    """PEP 427 escaping: runs of ``-_.`` become a single ``_`` in wheel file components."""
    return re.sub(r"[-_.]+", "_", project_name)


# Anchored to the expected distribution ON PURPOSE: a foreign wheel name is "not the
# IntentumDiff release format" (fails the regex), while a right-name/wrong-version wheel
# gets the more specific version error from the comparisons below.
_WHEEL_NAME_RE = re.compile(
    rf"^(?P<name>{re.escape(_filename_form(EXPECTED_DISTRIBUTION))})-"
    r"(?P<version>[^-]+)-(?P<python_tag>[^-]+)-"
    r"(?P<abi_tag>[^-]+)-(?P<platform_tag>[^-]+(?:\.[^-]+)*)\.whl$"
)


def _format_bytes(size: int) -> str:
    if size < BYTES_PER_MB:
        return f"{size} bytes"
    return f"{size / BYTES_PER_MB:.1f} MB"


def _find_wheel(path: Path) -> Path:
    if path.is_file():
        return path
    wheels = sorted(path.glob("*.whl"))
    if not wheels:
        raise ValueError(f"no wheel found in {path}")
    if len(wheels) != 1:
        names = ", ".join(wheel.name for wheel in wheels)
        raise ValueError(f"expected exactly one wheel in {path}, found {len(wheels)}: {names}")
    return wheels[0]


def _validate_archive_entry_name(name: str) -> None:
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ValueError(f"wheel contains unsafe archive path: {name!r}")


def _validate_wheel_entries(
    names: set[str],
    *,
    expected_name: str,
    expected_version: str,
) -> None:
    """Fail closed on unexpected or dangerous files in release wheels."""
    dist_info_prefix = f"{_filename_form(expected_name)}-{expected_version}.dist-info/"
    allowed_prefixes = ("intentumdiff/", dist_info_prefix)
    for name in names:
        _validate_archive_entry_name(name)
        lower = name.lower()
        basename = PurePosixPath(name).name.lower()
        if not name.startswith(allowed_prefixes):
            raise ValueError(f"wheel contains unexpected top-level entry: {name!r}")
        if basename in BLOCKED_ARCHIVE_BASENAMES:
            raise ValueError(f"wheel contains blocked sensitive/startup file: {name!r}")
        if lower.endswith(BLOCKED_ARCHIVE_SUFFIXES):
            raise ValueError(f"wheel contains blocked file type: {name!r}")
        if lower.endswith(".wasm") and not lower.startswith("intentumdiff/wasm/"):
            raise ValueError(f"wheel contains Wasm outside intentumdiff/wasm: {name!r}")


def _verify_wasm_provenance(zf: zipfile.ZipFile, names: set[str], wasm_files: list[str]) -> int:
    """Enforce the #89 provenance manifest when the wheel embeds one: every bundled `.wasm`
    must appear in the manifest with a matching SHA-256, and the manifest must list no artifact
    the wheel is missing (the #87 stale-artifact sweep as a package gate). Returns the number
    of verified artifacts, or ``-1`` when no manifest is embedded (optional this slice)."""
    if PROVENANCE_ENTRY not in names:
        return -1
    try:
        manifest = json.loads(zf.read(PROVENANCE_ENTRY).decode("utf-8"))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"wheel provenance manifest is not valid JSON: {exc}") from exc
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("wheel provenance manifest has no 'artifacts' map")

    expected = set(artifacts)
    staged = {PurePosixPath(name).name for name in wasm_files}
    stale = staged - expected
    if stale:
        raise ValueError(
            "wheel bundles wasm not in the provenance manifest (stale/extra): "
            + ", ".join(sorted(stale))
        )
    missing = expected - staged
    if missing:
        raise ValueError(
            "provenance manifest lists wasm the wheel is missing: "
            + ", ".join(sorted(missing))
        )
    for name in wasm_files:
        basename = PurePosixPath(name).name
        digest = hashlib.sha256(zf.read(name)).hexdigest()
        if digest != artifacts[basename].get("sha256"):
            raise ValueError(f"wheel wasm {basename} does not match its provenance SHA-256")
    return len(expected)


def verify_wheel(
    path: Path,
    *,
    max_wheel_bytes: int | None = DEFAULT_MAX_WHEEL_BYTES,
    expected_name: str = EXPECTED_DISTRIBUTION,
    expected_version: str = EXPECTED_VERSION,
) -> dict[str, int | str]:
    wheel = _find_wheel(path)
    match = _WHEEL_NAME_RE.match(wheel.name)
    if match is None:
        raise ValueError(f"wheel name does not match IntentumDiff release format: {wheel.name}")
    if match.group("name") != _filename_form(expected_name):
        raise ValueError(
            f"wheel name must be {_filename_form(expected_name)!r}: {wheel.name}"
        )
    if match.group("version") != expected_version:
        raise ValueError(
            f"wheel version must be {expected_version!r}: {wheel.name}"
        )
    platform_tag = match.group("platform_tag")

    size_bytes = wheel.stat().st_size
    if max_wheel_bytes is not None and size_bytes > max_wheel_bytes:
        raise ValueError(
            f"wheel {wheel.name} is {_format_bytes(size_bytes)}; "
            f"maximum allowed is {_format_bytes(max_wheel_bytes)}"
        )

    if "none-any" in wheel.name:
        raise ValueError("IntentumDiff release wheel must be native, not py3-none-any")

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    _validate_wheel_entries(
        names,
        expected_name=expected_name,
        expected_version=expected_version,
    )

    missing_packages = sorted(REQUIRED_PACKAGES - names)
    if missing_packages:
        raise ValueError(f"wheel is missing package files: {', '.join(missing_packages)}")

    native_modules = sorted(
        name
        for name in names
        if NATIVE_MODULE_NAME in name and name.endswith(NATIVE_SUFFIXES)
    )
    if len(native_modules) != 1:
        raise ValueError(
            "wheel must contain exactly one intentumdiff_rust_core native module; "
            f"found {len(native_modules)}"
        )
    native_entries = sorted(name for name in names if name.endswith(NATIVE_SUFFIXES))
    unexpected_native = sorted(set(native_entries) - set(native_modules))
    if unexpected_native:
        raise ValueError(
            "wheel contains unexpected native module(s): "
            + ", ".join(unexpected_native)
        )

    wasm_files = sorted(name for name in names if name.endswith(".wasm"))
    if not wasm_files:
        raise ValueError("wheel must contain built-in Wasm plugin assets")
    with zipfile.ZipFile(wheel) as zf:
        provenance_count = _verify_wasm_provenance(zf, names, wasm_files)

    metadata_files = sorted(name for name in names if name.endswith(".dist-info/METADATA"))
    if len(metadata_files) != 1:
        raise ValueError(f"wheel must contain exactly one METADATA file; found {len(metadata_files)}")
    wheel_files = sorted(name for name in names if name.endswith(".dist-info/WHEEL"))
    if len(wheel_files) != 1:
        raise ValueError(f"wheel must contain exactly one WHEEL file; found {len(wheel_files)}")

    with zipfile.ZipFile(wheel) as zf:
        metadata = Parser().parsestr(zf.read(metadata_files[0]).decode("utf-8", errors="replace"))
        wheel_metadata = Parser().parsestr(zf.read(wheel_files[0]).decode("utf-8", errors="replace"))
    if metadata.get("Name") != expected_name:
        raise ValueError(
            f"METADATA Name must be {expected_name!r}; found {metadata.get('Name')!r}"
        )
    if metadata.get("Version") != expected_version:
        raise ValueError(
            f"METADATA Version must be {expected_version!r}; found {metadata.get('Version')!r}"
        )
    wheel_tags = set(wheel_metadata.get_all("Tag", []))
    filename_tag = (
        f"{match.group('python_tag')}-{match.group('abi_tag')}-{platform_tag}"
    )
    if filename_tag not in wheel_tags:
        raise ValueError(
            f"WHEEL tags must include filename tag {filename_tag!r}; found {sorted(wheel_tags)!r}"
        )

    return {
        "wheel": wheel.name,
        "platform_tag": platform_tag,
        "native_modules": len(native_modules),
        "wasm_files": len(wasm_files),
        "provenance_verified": provenance_count,
        "metadata_files": len(metadata_files),
        "size_bytes": size_bytes,
    }


def verify_wheels(
    path: Path,
    *,
    max_wheel_bytes: int | None = DEFAULT_MAX_WHEEL_BYTES,
    max_release_bytes: int | None = DEFAULT_MAX_RELEASE_BYTES,
    expected_name: str = EXPECTED_DISTRIBUTION,
    expected_version: str = EXPECTED_VERSION,
    expected_platforms: set[str] | None = None,
    expected_platform_patterns: list[str] | None = None,
) -> list[dict[str, int | str]]:
    """Verify one wheel file or every wheel in a directory."""
    if path.is_file():
        wheels = [path]
    else:
        wheels = sorted(path.glob("*.whl"))
        if not wheels:
            raise ValueError(f"no wheel found in {path}")

    summaries = [
        verify_wheel(
            wheel,
            max_wheel_bytes=max_wheel_bytes,
            expected_name=expected_name,
            expected_version=expected_version,
        )
        for wheel in wheels
    ]
    if expected_platforms is not None:
        actual_platforms = {str(summary["platform_tag"]) for summary in summaries}
        if actual_platforms != expected_platforms:
            raise ValueError(
                "release wheel platform set mismatch: "
                f"expected {sorted(expected_platforms)!r}, found {sorted(actual_platforms)!r}"
            )
    if expected_platform_patterns:
        actual_platforms = [str(summary["platform_tag"]) for summary in summaries]
        for pattern in expected_platform_patterns:
            regex = re.compile(pattern)
            if not any(regex.fullmatch(platform) for platform in actual_platforms):
                raise ValueError(
                    "release wheel platform set mismatch: "
                    f"no platform matched {pattern!r}; found {sorted(actual_platforms)!r}"
                )
    total_size = sum(int(summary["size_bytes"]) for summary in summaries)
    if max_release_bytes is not None and total_size > max_release_bytes:
        raise ValueError(
            f"release wheel set is {_format_bytes(total_size)}; "
            f"maximum allowed is {_format_bytes(max_release_bytes)}"
        )
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Wheel file or directory containing release wheels")
    parser.add_argument(
        "--max-wheel-mb",
        type=float,
        default=DEFAULT_MAX_WHEEL_BYTES / BYTES_PER_MB,
        help="Maximum size for any single wheel in MB; set to 0 to disable",
    )
    parser.add_argument(
        "--max-release-mb",
        type=float,
        default=DEFAULT_MAX_RELEASE_BYTES / BYTES_PER_MB,
        help="Maximum total size for all wheels in MB; set to 0 to disable",
    )
    parser.add_argument("--expected-name", default=EXPECTED_DISTRIBUTION)
    parser.add_argument("--expected-version", default=EXPECTED_VERSION)
    parser.add_argument(
        "--expected-platform",
        action="append",
        default=[],
        help="Expected wheel platform tag; repeat to enforce the full release set",
    )
    parser.add_argument(
        "--expected-platform-pattern",
        action="append",
        default=[],
        help="Expected platform regex; repeat to enforce release platform families",
    )
    args = parser.parse_args(argv)
    if args.max_wheel_mb < 0 or args.max_release_mb < 0:
        parser.error("size limits must be non-negative")
    max_wheel_bytes = (
        None if args.max_wheel_mb == 0 else int(args.max_wheel_mb * BYTES_PER_MB)
    )
    max_release_bytes = (
        None if args.max_release_mb == 0 else int(args.max_release_mb * BYTES_PER_MB)
    )

    try:
        summaries = verify_wheels(
            args.path,
            max_wheel_bytes=max_wheel_bytes,
            max_release_bytes=max_release_bytes,
            expected_name=args.expected_name,
            expected_version=args.expected_version,
            expected_platforms=set(args.expected_platform) if args.expected_platform else None,
            expected_platform_patterns=args.expected_platform_pattern or None,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for summary in summaries:
        print(
            "Verified {wheel}: {native_modules} native module, "
            "{wasm_files} Wasm files, {size}".format(
                size=_format_bytes(int(summary["size_bytes"])),
                **summary
            )
        )
    if len(summaries) > 1:
        total_size = sum(int(summary["size_bytes"]) for summary in summaries)
        print(
            f"Verified {len(summaries)} IntentumDiff release wheels; "
            f"total size {_format_bytes(total_size)}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
