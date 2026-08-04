"""Thin Python bridge for Rust-owned perceptual asset diffs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intentumdiff.rust_core import _load_backend


def diff_image_assets(
    *,
    before_path: str | Path,
    after_path: str | Path,
    out_dir: str | Path,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend = _load_backend()
    raw = backend.diff_asset_image_json(
        str(Path(before_path)),
        str(Path(after_path)),
        str(Path(out_dir)),
        json.dumps(options or {}, separators=(",", ":")),
    )
    return json.loads(raw)


def diff_git_assets(
    *,
    repo_path: str | Path,
    base: str,
    head: str,
    out_dir: str | Path,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend = _load_backend()
    raw = backend.diff_git_assets_json(
        str(Path(repo_path)),
        base,
        head,
        str(Path(out_dir)),
        json.dumps(options or {}, separators=(",", ":")),
    )
    return json.loads(raw)
