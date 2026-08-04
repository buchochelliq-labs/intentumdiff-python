from __future__ import annotations

import hashlib as _hashlib
import json as _json
import tomllib
import zipfile
from pathlib import Path

import pytest

from scripts.verify_intentumdiff_wheel import verify_wheel, verify_wheels


def _write_wheel(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            if name.endswith(".dist-info/METADATA"):
                zf.writestr(
                    name,
                    "Metadata-Version: 2.3\n"
                    "Name: intentumdiff-python\n"
                    "Version: 0.0.1\n",
                )
            elif name.endswith(".dist-info/WHEEL"):
                tag = "-".join(path.name.removesuffix(".whl").split("-")[-3:])
                zf.writestr(
                    name,
                    "Wheel-Version: 1.0\n"
                    "Generator: test\n"
                    "Root-Is-Purelib: false\n"
                    f"Tag: {tag}\n",
                )
            else:
                zf.writestr(name, "")
    return path


def _valid_names() -> list[str]:
    return [
        "intentumdiff/__init__.py",
        "intentumdiff/wasm/python_parser.wasm",
        "intentumdiff/intentumdiff_rust_core.cp312-win_amd64.pyd",
        "intentumdiff_python-0.0.1.dist-info/METADATA",
        "intentumdiff_python-0.0.1.dist-info/WHEEL",
    ]


def test_verify_wheel_accepts_native_intentumdiff_wheel(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )

    summary = verify_wheel(wheel)

    assert summary["wheel"] == wheel.name
    assert summary["native_modules"] == 1
    assert summary["wasm_files"] == 1
    assert summary["platform_tag"] == "win_amd64"
    assert int(summary["size_bytes"]) > 0


def test_verify_wheel_accepts_windows_arm64_wheel(tmp_path: Path) -> None:
    names = [
        name.replace("win_amd64", "win_arm64")
        for name in _valid_names()
    ]
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-abi3-win_arm64.whl",
        names,
    )

    summary = verify_wheel(wheel)

    assert summary["platform_tag"] == "win_arm64"
    assert summary["native_modules"] == 1


def test_verify_wheel_rejects_pure_python_wheel(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-py3-none-any.whl",
        _valid_names(),
    )

    with pytest.raises(ValueError, match="must be native"):
        verify_wheel(wheel)


def test_verify_wheel_rejects_missing_rust_core(tmp_path: Path) -> None:
    names = [name for name in _valid_names() if "intentumdiff_rust_core" not in name]
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        names,
    )

    with pytest.raises(ValueError, match="native module"):
        verify_wheel(wheel)


def test_verify_wheel_rejects_missing_wasm_assets(tmp_path: Path) -> None:
    names = [name for name in _valid_names() if not name.endswith(".wasm")]
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        names,
    )

    with pytest.raises(ValueError, match="Wasm plugin assets"):
        verify_wheel(wheel)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("evil/__init__.py", "unexpected top-level"),
        ("intentumdiff/../evil.py", "unsafe archive path"),
        ("intentumdiff/sitecustomize.py", "sensitive/startup"),
        ("intentumdiff/.env", "sensitive/startup"),
        ("intentumdiff/payload.pth", "blocked file type"),
        ("intentumdiff/scripts/postinstall.ps1", "blocked file type"),
        ("intentumdiff/plugins/unexpected.wasm", "Wasm outside"),
        ("intentumdiff/extra_native.cp312-win_amd64.pyd", "unexpected native"),
    ],
)
def test_verify_wheel_rejects_dangerous_extra_entries(
    tmp_path: Path,
    entry: str,
    message: str,
) -> None:
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        [*_valid_names(), entry],
    )

    with pytest.raises(ValueError, match=message):
        verify_wheel(wheel)


def test_verify_wheel_rejects_oversized_single_wheel(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )

    with pytest.raises(ValueError, match="maximum allowed"):
        verify_wheel(wheel, max_wheel_bytes=1)


def test_verify_wheels_rejects_oversized_release_set(tmp_path: Path) -> None:
    _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )
    _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-macosx_11_0_arm64.whl",
        _valid_names(),
    )

    with pytest.raises(ValueError, match="release wheel set"):
        verify_wheels(tmp_path, max_release_bytes=1)


def test_verify_wheel_rejects_wrong_distribution_name(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "notintentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )

    with pytest.raises(ValueError, match="release format"):
        verify_wheel(wheel)


def test_verify_wheel_rejects_wrong_version(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "intentumdiff_python-0.2.0-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )

    with pytest.raises(ValueError, match="wheel version"):
        verify_wheel(wheel)


def test_verify_wheel_rejects_metadata_identity_mismatch(tmp_path: Path) -> None:
    wheel = tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        for name in _valid_names():
            if name.endswith(".dist-info/METADATA"):
                zf.writestr(name, "Metadata-Version: 2.3\nName: other\nVersion: 0.0.1\n")
            elif name.endswith(".dist-info/WHEEL"):
                zf.writestr(
                    name,
                    "Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp312-cp312-win_amd64\n",
                )
            else:
                zf.writestr(name, "")

    with pytest.raises(ValueError, match="METADATA Name"):
        verify_wheel(wheel)


def test_verify_wheels_rejects_unexpected_platform_set(tmp_path: Path) -> None:
    _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )

    with pytest.raises(ValueError, match="platform set mismatch"):
        verify_wheels(tmp_path, expected_platforms={"manylinux_2_28_x86_64"})


def test_verify_wheels_accepts_expected_platform_patterns(tmp_path: Path) -> None:
    _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl",
        _valid_names(),
    )
    _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-abi3-win_arm64.whl",
        [name.replace("win_amd64", "win_arm64") for name in _valid_names()],
    )
    _write_wheel(
        tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-macosx_11_0_arm64.whl",
        _valid_names(),
    )

    summaries = verify_wheels(
        tmp_path,
        expected_platform_patterns=[r"win_amd64", r"win_arm64", r"macosx_.*_arm64"],
    )

    assert len(summaries) == 3


def test_pyproject_uses_top_level_maturin_native_wheel_backend() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "intentumdiff-python"
    assert pyproject["project"]["authors"] == [{"name": "BuchochelliQ Labs"}]
    assert pyproject["project"]["maintainers"] == [{"name": "BuchochelliQ Labs"}]
    assert pyproject["build-system"]["build-backend"] == "maturin"
    # Suffix match: the monorepo points at crates/rust-core-host directly; the #82 split
    # python repo points at the PROVISIONED engine checkout (build/intentumdiff-core/crates/…).
    assert pyproject["tool"]["maturin"]["manifest-path"].endswith(
        "crates/rust-core-host/Cargo.toml"
    )
    assert pyproject["tool"]["maturin"]["module-name"] == "intentumdiff.intentumdiff_rust_core"
    assert pyproject["tool"]["maturin"]["python-packages"] == ["intentumdiff"]


# --- #89 provenance manifest gate ---------------------------------------------------------


def _write_wheel_with_provenance(
    path: Path,
    wasm: dict[str, bytes],
    manifest_artifacts: dict[str, dict] | None,
) -> Path:
    """A wheel whose wasm entries carry real bytes, optionally embedding a provenance manifest
    (None = no manifest embedded, the optional-this-slice case)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("intentumdiff/__init__.py", "")
        zf.writestr("intentumdiff/intentumdiff_rust_core.cp312-win_amd64.pyd", "")
        for name, data in wasm.items():
            zf.writestr(f"intentumdiff/wasm/{name}", data)
        if manifest_artifacts is not None:
            zf.writestr(
                "intentumdiff/wasm/wasm_provenance.json",
                _json.dumps({"schema_version": 1, "artifacts": manifest_artifacts}),
            )
        zf.writestr(
            "intentumdiff_python-0.0.1.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: intentumdiff-python\nVersion: 0.0.1\n",
        )
        tag = "-".join(path.name.removesuffix(".whl").split("-")[-3:])
        zf.writestr(
            "intentumdiff_python-0.0.1.dist-info/WHEEL",
            f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: false\nTag: {tag}\n",
        )
    return path


def _artifacts(wasm: dict[str, bytes]) -> dict[str, dict]:
    return {
        name: {"sha256": _hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        for name, data in wasm.items()
    }


def _prov_wheel_name(tmp_path: Path) -> Path:
    return tmp_path / "intentumdiff_python-0.0.1-cp312-cp312-win_amd64.whl"


def test_provenance_absent_is_optional_this_slice(tmp_path: Path) -> None:
    wasm = {"python_parser.wasm": b"\x00asm py"}
    wheel = _write_wheel_with_provenance(_prov_wheel_name(tmp_path), wasm, None)
    summary = verify_wheel(wheel)
    assert summary["provenance_verified"] == -1  # -1 = no manifest embedded


def test_provenance_matching_manifest_passes(tmp_path: Path) -> None:
    wasm = {"python_parser.wasm": b"\x00asm py", "css_parser.wasm": b"\x00asm css"}
    wheel = _write_wheel_with_provenance(_prov_wheel_name(tmp_path), wasm, _artifacts(wasm))
    summary = verify_wheel(wheel)
    assert summary["provenance_verified"] == 2


def test_provenance_tampered_sha_is_rejected(tmp_path: Path) -> None:
    wasm = {"python_parser.wasm": b"\x00asm py"}
    manifest = _artifacts({"python_parser.wasm": b"DIFFERENT BYTES"})
    wheel = _write_wheel_with_provenance(_prov_wheel_name(tmp_path), wasm, manifest)
    with pytest.raises(ValueError, match="does not match its provenance SHA-256"):
        verify_wheel(wheel)


def test_provenance_stale_extra_wasm_is_rejected(tmp_path: Path) -> None:
    wasm = {"python_parser.wasm": b"\x00asm py", "py_semantic_diff_stale.wasm": b"stale"}
    manifest = _artifacts({"python_parser.wasm": b"\x00asm py"})  # manifest never saw the stale one
    wheel = _write_wheel_with_provenance(_prov_wheel_name(tmp_path), wasm, manifest)
    with pytest.raises(ValueError, match="stale/extra"):
        verify_wheel(wheel)


def test_provenance_missing_wasm_is_rejected(tmp_path: Path) -> None:
    wasm = {"python_parser.wasm": b"\x00asm py"}
    manifest = _artifacts({"python_parser.wasm": b"\x00asm py", "gone_parser.wasm": b"gone"})
    wheel = _write_wheel_with_provenance(_prov_wheel_name(tmp_path), wasm, manifest)
    with pytest.raises(ValueError, match="missing"):
        verify_wheel(wheel)
