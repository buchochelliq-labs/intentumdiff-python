from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from scripts.validate_release_media_manifest import validate_manifest


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_minimal_png(path: Path, width: int = 1280, height: int = 720) -> None:
    path.write_bytes(
        PNG_SIGNATURE
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def write_manifest(path: Path, screenshot_path: Path) -> None:
    payload = {
        "screenshots": [
            {
                "surface": "review",
                "status": "approved",
                "screenshot_path": screenshot_path.as_posix(),
                "capture_region": "80,80,1280,720",
                "captured_width": 1280,
                "captured_height": 720,
                "capture_command": "powershell -File scripts/record-release-demo.ps1 -Scene review -CaptureMode screenshot",
            }
        ]
    }
    path.write_text("\ufeff" + json.dumps(payload), encoding="utf-8")


def write_manifest_with_status(path: Path, screenshot_path: Path, status: str) -> None:
    payload = {
        "screenshots": [
            {
                "surface": "review",
                "status": status,
                "screenshot_path": screenshot_path.as_posix(),
                "capture_region": "80,80,1280,720",
                "captured_width": 1280,
                "captured_height": 720,
                "capture_command": "powershell -File scripts/record-release-demo.ps1 -Scene review -CaptureMode screenshot",
            }
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_release_media_validator_accepts_bom_and_partial_smoke_manifest(tmp_path: Path) -> None:
    screenshot = tmp_path / "intentumdiff-vscode-review-smoke.png"
    manifest = tmp_path / "manifest.json"
    write_minimal_png(screenshot)
    write_manifest(manifest, screenshot)

    assert validate_manifest(manifest, require_all_surfaces=False) == 1


def test_release_media_validator_stays_strict_by_default_for_missing_surfaces(tmp_path: Path) -> None:
    screenshot = tmp_path / "intentumdiff-vscode-review-smoke.png"
    manifest = tmp_path / "manifest.json"
    write_minimal_png(screenshot)
    write_manifest(manifest, screenshot)

    with pytest.raises(AssertionError, match="missing required surfaces"):
        validate_manifest(manifest)


def test_release_media_validator_requires_required_surfaces_to_be_approved(tmp_path: Path) -> None:
    screenshot = tmp_path / "intentumdiff-vscode-review-smoke.png"
    manifest = tmp_path / "manifest.json"
    write_minimal_png(screenshot)
    write_manifest_with_status(manifest, screenshot, "needs_polish")

    with pytest.raises(AssertionError, match="must be approved"):
        validate_manifest(manifest, require_all_surfaces=False)

    assert (
        validate_manifest(
            manifest,
            require_all_surfaces=False,
            require_approved_required_surfaces=False,
        )
        == 1
    )
