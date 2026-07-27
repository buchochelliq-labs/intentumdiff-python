from __future__ import annotations

import json
from pathlib import Path


def test_diff_image_assets_is_thin_native_bridge(monkeypatch) -> None:
    from intentdiff import assets

    calls: list[tuple[str, str, str, str]] = []

    class Backend:
        def diff_asset_image_json(
            self,
            before: str,
            after: str,
            out_dir: str,
            options_json: str,
        ) -> str:
            calls.append((before, after, out_dir, options_json))
            return json.dumps({"kind": "asset_diff", "status": "compared"})

    monkeypatch.setattr(assets, "_load_backend", lambda: Backend())

    result = assets.diff_image_assets(
        before_path=Path("before.png"),
        after_path=Path("after.png"),
        out_dir=Path("out"),
        options={"pixel_threshold": 7},
    )

    assert result == {"kind": "asset_diff", "status": "compared"}
    assert calls == [("before.png", "after.png", "out", '{"pixel_threshold":7}')]


def test_diff_git_assets_is_thin_native_bridge(monkeypatch) -> None:
    from intentdiff import assets

    calls: list[tuple[str, str, str, str, str]] = []

    class Backend:
        def diff_git_assets_json(
            self,
            repo: str,
            base: str,
            head: str,
            out_dir: str,
            options_json: str,
        ) -> str:
            calls.append((repo, base, head, out_dir, options_json))
            return json.dumps({"kind": "asset_diff_batch", "asset_diffs": []})

    monkeypatch.setattr(assets, "_load_backend", lambda: Backend())

    result = assets.diff_git_assets(
        repo_path=Path("."),
        base="main",
        head="HEAD",
        out_dir=Path("out"),
        options={"dimension_policy": "pad"},
    )

    assert result == {"kind": "asset_diff_batch", "asset_diffs": []}
    assert calls == [(".", "main", "HEAD", "out", '{"dimension_policy":"pad"}')]
