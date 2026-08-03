"""Collect downloaded release wheels into a flat publish directory."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def prepare_release_dist(source_dir: Path, output_dir: Path) -> list[Path]:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise ValueError(f"source directory does not exist: {source_dir}")

    wheels = sorted(source_dir.rglob("*.whl"))
    if not wheels:
        raise ValueError(f"no wheels found under {source_dir}")

    seen_names: set[str] = set()
    copied: list[Path] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        if wheel.name in seen_names:
            raise ValueError(f"duplicate wheel name in release artifacts: {wheel.name}")
        seen_names.add(wheel.name)
        destination = output_dir / wheel.name
        if wheel.resolve() == destination:
            copied.append(destination)
            continue
        shutil.copy2(wheel, destination)
        copied.append(destination)
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="Downloaded artifact directory")
    parser.add_argument("output_dir", type=Path, help="Flat directory for publishable wheels")
    args = parser.parse_args(argv)

    try:
        copied = prepare_release_dist(args.source_dir, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for wheel in copied:
        print(f"Prepared {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
