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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core-dir")
    ap.add_argument("--wasm-dir")
    args = ap.parse_args()
    stage_core(args.core_dir)
    stage_wasm(args.wasm_dir)
    print("build inputs ready: `maturin build --release -b cffi` (or pip install -e .)")


if __name__ == "__main__":
    main()
