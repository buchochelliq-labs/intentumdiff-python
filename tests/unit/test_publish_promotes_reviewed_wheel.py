"""#279: the release must publish the reviewed wheel bytes, never a fresh rebuild.

The 0.0.2 candidate wheel was verified byte-for-byte and exercised by real-runtime VS Code
acceptance, but publish.yml rebuilt from the tag with a different command than CI. These
checks pin the promotion contract on the workflow text.
"""

from __future__ import annotations

from pathlib import Path

_PUBLISH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish.yml"


def test_publish_does_not_rebuild_the_wheel() -> None:
    text = _PUBLISH.read_text(encoding="utf-8")
    assert "maturin build" not in text, "publish.yml rebuilds the wheel instead of promoting it"
    assert "pip wheel" not in text, "publish.yml rebuilds the wheel instead of promoting it"


def test_publish_verifies_promoted_artifact_hashes() -> None:
    text = _PUBLISH.read_text(encoding="utf-8")
    assert "sha256sum -c" in text or "hashlib.sha256" in text or "--expected-sha256" in text, (
        "publish.yml must verify each promoted wheel against its reviewed sha256"
    )
