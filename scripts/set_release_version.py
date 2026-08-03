"""Set the project version in pyproject.toml for CI release rehearsals.

This is intentionally narrow: it edits only the repository-level project
version used by maturin wheel metadata. Production releases keep the committed
version in pyproject.toml; TestPyPI rehearsals may call this in CI to avoid
reusing filenames that TestPyPI has already tombstoned.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PEP440_VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:(?:a|b|rc)(?:0|[1-9]\d*)|\.post(?:0|[1-9]\d*)|\.dev(?:0|[1-9]\d*))?$"
)
PROJECT_VERSION_RE = re.compile(
    r'(?ms)^(\[project\]\s*(?:(?!^\[).)*?^version\s*=\s*)"[^"]+"$'
)


def set_project_version(pyproject_path: Path, version: str) -> None:
    if PEP440_VERSION_RE.fullmatch(version) is None:
        raise ValueError(f"unsupported release version: {version!r}")

    text = pyproject_path.read_text(encoding="utf-8")
    updated, count = PROJECT_VERSION_RE.subn(rf'\g<1>"{version}"', text, count=1)
    if count != 1:
        raise ValueError(f"could not find a project version in {pyproject_path}")
    pyproject_path.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="PEP 440 package version to stamp")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Project metadata file to update (default: pyproject.toml)",
    )
    args = parser.parse_args(argv)

    try:
        set_project_version(args.pyproject, args.version)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Set {args.pyproject} project version to {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
