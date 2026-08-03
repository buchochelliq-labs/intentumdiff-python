from __future__ import annotations

from pathlib import Path

import pytest

from scripts.prepare_release_dist import prepare_release_dist


def test_prepare_release_dist_flattens_nested_wheels(tmp_path: Path) -> None:
    source = tmp_path / "downloaded-dist"
    nested = source / "dist"
    nested.mkdir(parents=True)
    wheel = nested / "intentdiff-0.0.1-cp312-abi3-win_amd64.whl"
    wheel.write_bytes(b"wheel")

    output = tmp_path / "publish-dist"
    copied = prepare_release_dist(source, output)

    assert copied == [output / wheel.name]
    assert (output / wheel.name).read_bytes() == b"wheel"


def test_prepare_release_dist_rejects_missing_wheels(tmp_path: Path) -> None:
    source = tmp_path / "downloaded-dist"
    source.mkdir()

    with pytest.raises(ValueError, match="no wheels"):
        prepare_release_dist(source, tmp_path / "publish-dist")


def test_prepare_release_dist_rejects_duplicate_wheel_names(tmp_path: Path) -> None:
    source = tmp_path / "downloaded-dist"
    first = source / "linux"
    second = source / "windows"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    wheel_name = "intentdiff-0.0.1-cp312-abi3-win_amd64.whl"
    (first / wheel_name).write_bytes(b"one")
    (second / wheel_name).write_bytes(b"two")

    with pytest.raises(ValueError, match="duplicate wheel"):
        prepare_release_dist(source, tmp_path / "publish-dist")
