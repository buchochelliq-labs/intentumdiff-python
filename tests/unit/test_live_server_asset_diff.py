"""Acceptance tests for the live-server ``asset_diff`` op (intentumdiff-vscode#25).

The Rust core owns this behaviour and pins it with its own ``#[cfg(test)]`` tests; these
prove the guarantee survives the binding — that an editor sending a repo-relative path and a
ref really does get the engine's artifact manifest back over the protocol, rather than a
plausible-looking summary assembled on this side of the boundary.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Reason string matches the skip ratchet's existing "platform" class (skip_reasons_baseline.json)
# rather than minting a new one — a new phrasing for an already-classified condition is exactly
# what that gate exists to stop.
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

# Every layer the perceptual viewer can render. The panel reported "perceptual diff pending"
# for as long as nothing asked the engine for these, so the test asserts all of them.
_ARTIFACT_LAYERS = ("before", "after", "diff", "heatmap", "mask", "overlay", "contact_sheet")


def _png_bytes(pixels: list[tuple[int, int, int]], width: int, height: int) -> bytes:
    """A minimal RGB PNG, so the fixture needs no image library on the Python side."""
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBB", *pixels[y * width + x]) for x in range(width))
        for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _checkerboard(width: int, height: int) -> list[tuple[int, int, int]]:
    return [
        (200, 40, 90) if (x // 4 + y // 4) % 2 else (30, 60, 140)
        for y in range(height)
        for x in range(width)
    ]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def image_repo(tmp_path: Path) -> Path:
    """A git repo whose committed image differs from its working-tree copy."""
    repo = tmp_path / "repo"
    (repo / "assets").mkdir(parents=True)
    card = repo / "assets" / "card.png"
    width, height = 32, 32
    card.write_bytes(_png_bytes(_checkerboard(width, height), width, height))
    _git(repo, "init")
    _git(repo, "config", "user.email", "intentumdiff@example.test")
    _git(repo, "config", "user.name", "IntentumDiff Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    changed = _checkerboard(width, height)
    for y in range(8, 22):
        for x in range(6, 20):
            changed[y * width + x] = (250, 250, 40)
    card.write_bytes(_png_bytes(changed, width, height))
    return repo


def _server(repo: Path, ref: str = "HEAD"):
    from intentumdiff.live_server import LiveServer

    differ = MagicMock()
    differ._config = MagicMock()
    differ._registry = MagicMock()
    return LiveServer(differ, repo_path=str(repo), ref=ref)


def _asset_diff(repo: Path, request: dict, ref: str = "HEAD") -> dict:
    sent: list[dict] = []
    _server(repo, ref)._process_request({"op": "asset_diff", "seq": 7, **request}, sent.append)
    assert len(sent) == 1, sent
    return sent[0]


class TestAssetDiffOp:
    def test_tracked_image_returns_the_engines_artifact_manifest(self, image_repo: Path) -> None:
        response = _asset_diff(image_repo, {"path": "assets/card.png"})

        assert response["ok"] is True
        assert response["op"] == "asset_diff"
        assert response["seq"] == 7
        result = response["result"]
        assert result["status"] == "compared"
        assert result["file_path"] == "assets/card.png"

        for layer in _ARTIFACT_LAYERS:
            artifact = result["artifacts"].get(layer)
            assert artifact, f"engine did not report the {layer} artifact"
            assert Path(artifact).is_file(), f"{layer} announced at {artifact} but not written"

        # The overlay geometry the viewer draws is only meaningful with these three together.
        assert result["comparison_dimensions"] == {"width": 32, "height": 32}
        assert result["hotspots"], "a 14x14 block change should surface at least one hotspot"
        assert result["histograms"]["red_delta"]
        assert result["changed_pixel_percentage"] > 0

    def test_artifacts_land_in_a_cache_that_ignores_itself(self, image_repo: Path) -> None:
        response = _asset_diff(image_repo, {"path": "assets/card.png"})

        cache = image_repo / ".intentumdiff-cache"
        artifact = Path(response["result"]["artifacts"]["heatmap"]).resolve()
        assert cache.resolve() in artifact.parents, "artifacts must not land in the work tree"
        assert "*" in (cache / ".gitignore").read_text(encoding="utf-8")

    def test_added_image_is_reported_as_skipped_with_a_reason(self, image_repo: Path) -> None:
        (image_repo / "assets" / "new.png").write_bytes(_png_bytes(_checkerboard(8, 8), 8, 8))

        result = _asset_diff(image_repo, {"path": "assets/new.png"})["result"]

        # An added image has nothing to compare against. Saying so is the point: the state this
        # replaced claimed a comparison was merely "pending" and never produced one.
        assert result["status"] == "skipped"
        assert result["change_type"] == "A"
        assert "no before" in result["reason"]
        assert not result.get("artifacts")

    def test_deleted_image_is_reported_as_skipped(self, image_repo: Path) -> None:
        (image_repo / "assets" / "card.png").unlink()

        result = _asset_diff(image_repo, {"path": "assets/card.png"})["result"]

        assert result["status"] == "skipped"
        assert result["change_type"] == "D"

    def test_unresolvable_ref_is_an_error_not_an_added_image(self, image_repo: Path) -> None:
        response = _asset_diff(image_repo, {"path": "assets/card.png", "ref": "no-such-ref"})

        assert response["ok"] is False
        assert "git rev not found" in response["error"]["message"]

    @pytest.mark.parametrize(
        "path",
        ["../outside.png", "..\\outside.png", "assets/../../outside.png"],
    )
    def test_path_escaping_the_served_repo_is_refused(self, image_repo: Path, path: str) -> None:
        response = _asset_diff(image_repo, {"path": path})

        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_request"

    def test_request_without_a_path_or_pair_is_refused(self, image_repo: Path) -> None:
        response = _asset_diff(image_repo, {})

        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_request"

    def test_explicit_before_after_paths_still_work(self, image_repo: Path) -> None:
        before = image_repo / "assets" / "before.png"
        after = image_repo / "assets" / "after.png"
        pixels = _checkerboard(16, 16)
        before.write_bytes(_png_bytes(pixels, 16, 16))
        changed = list(pixels)
        for index in range(20, 60):
            changed[index] = (255, 255, 255)
        after.write_bytes(_png_bytes(changed, 16, 16))

        result = _asset_diff(
            image_repo,
            {"before_path": "assets/before.png", "after_path": "assets/after.png"},
        )["result"]

        assert result["status"] == "compared"
        assert result["artifacts"]["overlay"]

    def test_serving_a_subdirectory_still_resolves_the_base_blob(self, image_repo: Path) -> None:
        # A monorepo subfolder opened as a workspace addresses files relative to itself; git
        # addresses blobs from the repository root.
        result = _asset_diff(image_repo / "assets", {"path": "card.png"})["result"]

        assert result["status"] == "compared"
        assert result["file_path"] == "card.png"

    def test_a_users_own_cache_gitignore_is_left_alone(self, image_repo: Path) -> None:
        _asset_diff(image_repo, {"path": "assets/card.png"})
        marker = image_repo / ".intentumdiff-cache" / ".gitignore"
        marker.write_text("mine\n", encoding="utf-8")

        _asset_diff(image_repo, {"path": "assets/card.png"})

        assert marker.read_text(encoding="utf-8") == "mine\n"

    def test_a_non_image_path_is_an_error_not_a_comparison(self, image_repo: Path) -> None:
        (image_repo / "notes.txt").write_text("not an image", encoding="utf-8")

        response = _asset_diff(image_repo, {"path": "notes.txt"})

        assert response["ok"] is False
        assert "asset diff failed" in response["error"]["message"]


class TestAssetDiffContract:
    """The op is served by BOTH live-servers from one core implementation.

    The Python server and the native binary (``crates/live-server``) call the same
    ``live_handle_asset_diff``, so what they advertise and what they answer cannot drift apart —
    which matters because the extension prefers the native binary whenever one is bundled, and
    for a while that binary had no arm for this op at all.
    """

    def test_capabilities_advertise_the_asset_diff_op(self, image_repo: Path) -> None:
        capabilities = _server(image_repo)._capabilities()

        assert "asset_diff" in capabilities["operations"]

    def test_the_response_comes_from_the_core_not_this_layer(self, image_repo: Path) -> None:
        from intentumdiff import rust_core

        request = {"op": "asset_diff", "seq": 7, "path": "assets/card.png"}
        expected = rust_core.live_handle_asset_diff(str(image_repo), "HEAD", request, 7)
        served = _asset_diff(image_repo, {"path": "assets/card.png"})

        # Same request, same engine, same content-addressed output -> identical answer.
        assert served == expected
