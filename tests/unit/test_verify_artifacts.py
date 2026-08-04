from __future__ import annotations

import argparse
from pathlib import Path

from scripts import verify_artifacts


def _args(root: Path, manifest: Path, patterns: list[str]) -> argparse.Namespace:
    return argparse.Namespace(
        root=str(root),
        manifest=str(manifest),
        patterns=patterns,
    )


def test_record_and_verify_artifacts_relative_to_custom_root(tmp_path: Path) -> None:
    root = tmp_path / "downloaded-dist"
    root.mkdir()
    wheel = root / "intentumdiff-0.0.1-cp312-cp312-win_amd64.whl"
    wheel.write_bytes(b"wheel")
    manifest = root / "artifacts-windows.sha256"

    assert verify_artifacts.cmd_record(_args(root, manifest, ["*.whl"])) == 0
    assert "intentumdiff-0.0.1-cp312-cp312-win_amd64.whl" in manifest.read_text(
        encoding="utf-8"
    )
    assert verify_artifacts.cmd_verify(_args(root, manifest, [])) == 0

    wheel.write_bytes(b"tampered")

    assert verify_artifacts.cmd_verify(_args(root, manifest, [])) == 1
