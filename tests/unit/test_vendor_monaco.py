from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_vendor_monaco():
    script = Path(__file__).resolve().parents[2] / "scripts" / "vendor_monaco.py"
    spec = importlib.util.spec_from_file_location("vendor_monaco", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_monaco_member_target_stays_under_destination(tmp_path: Path) -> None:
    vendor_monaco = _load_vendor_monaco()

    target = vendor_monaco._target_for_member(
        "package/min/vs/loader.js",
        "package/min/vs/",
        tmp_path,
    )

    assert target == (tmp_path / "min" / "vs" / "loader.js").resolve()


@pytest.mark.parametrize(
    "member_name",
    [
        "package/min/vs/../evil.js",
        "package/min/vs/C:/evil.js",
        "package/min/vs/evil\\loader.js",
    ],
)
def test_monaco_member_target_rejects_unsafe_paths(
    tmp_path: Path,
    member_name: str,
) -> None:
    vendor_monaco = _load_vendor_monaco()

    with pytest.raises(SystemExit):
        vendor_monaco._target_for_member(member_name, "package/min/vs/", tmp_path)


def test_monaco_download_rejects_non_https() -> None:
    vendor_monaco = _load_vendor_monaco()

    with pytest.raises(SystemExit):
        vendor_monaco._download("http://example.invalid/monaco.tgz")
