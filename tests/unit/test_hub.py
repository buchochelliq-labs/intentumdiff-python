"""
tests/unit/test_hub.py
~~~~~~~~~~~~~~~~~~~~~

Unit tests for intentumdiff.plugins.hub security features introduced in
the Phase 1+3 security hardening:

  * pip_install --no-deps (when dep_hashes empty)
  * pip_install --require-hashes (when dep_hashes non-empty)
  * _lint_wheel_contents rejects .pth, sitecustomize.py, .pyd, .so, .dll
  * pre_install_security_check fail-closed when wasm_checksums non-empty
    but wheel contains no .wasm files
  * PluginSpec.dep_hashes round-trips through load/save
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(**kw):
    from intentumdiff.plugins.hub import PluginSpec

    kw.setdefault("name", "test-plugin")
    return PluginSpec(**kw)


def _hash(ch: str) -> str:
    return "sha256:" + ch * 64


def _build_wheel(
    tmp_path: Path,
    files: dict[str, bytes],
    filename: str = "test_plugin-1.0.0-py3-none-any.whl",
) -> Path:
    """Create a minimal wheel ZIP at *tmp_path* with the given entries."""
    wheel_path = tmp_path / filename
    with zipfile.ZipFile(wheel_path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return wheel_path


def _write_clean_wheelhouse(dest: Path, *wheel_names: str) -> None:
    for wheel_name in wheel_names:
        _build_wheel(
            dest,
            {"pkg/__init__.py": b""},
            filename=wheel_name,
        )


# ---------------------------------------------------------------------------
# pip_install: --no-deps and --require-hashes
# ---------------------------------------------------------------------------


class TestDepHashesValidation:
    def test_extra_package_without_allowlist_is_rejected(self):
        from intentumdiff.plugins.hub import _validate_dep_hashes

        spec = _make_spec(
            dep_hashes={
                "my-plugin==1.0": _hash("a"),
                "unexpected-startup-hook==9.9": _hash("b"),
            }
        )

        errors = _validate_dep_hashes(spec, "my-plugin==1.0")

        assert any("allowed_dependencies" in error for error in errors)

    def test_allowlisted_extra_package_is_accepted(self):
        from intentumdiff.plugins.hub import _validate_dep_hashes

        spec = _make_spec(
            allowed_dependencies=["dep-a"],
            dep_hashes={
                "my-plugin==1.0": _hash("a"),
                "dep-a==2.0": _hash("b"),
            },
        )

        assert _validate_dep_hashes(spec, "my-plugin==1.0") == []

    def test_invalid_hash_value_is_rejected(self):
        from intentumdiff.plugins.hub import _validate_dep_hashes

        spec = _make_spec(dep_hashes={"my-plugin==1.0": "sha256:aaaa"})

        errors = _validate_dep_hashes(spec, "my-plugin==1.0")

        assert any("64-character hex" in error for error in errors)


class TestPluginSpecValidation:
    @pytest.mark.parametrize(
        "name",
        [
            "plugin @ https://example.test/plugin.whl",
            "../plugin",
            "https://example.test/plugin",
            "",
        ],
    )
    def test_plugin_name_rejects_requirement_url_and_path_shapes(self, name: str) -> None:
        from intentumdiff.plugins.hub import PluginSpec

        with pytest.raises(ValueError, match="Invalid plugin name"):
            PluginSpec(name=name)

    def test_plugin_source_rejects_unknown_source(self) -> None:
        from intentumdiff.plugins.hub import PluginSpec

        with pytest.raises(ValueError, match="Invalid plugin source"):
            PluginSpec(name="test-plugin", source="direct")


class TestPipInstall:
    def test_no_dep_hashes_adds_no_deps_flag(self):
        """pip_install with empty dep_hashes must pass --no-deps to pip."""
        from intentumdiff.plugins.hub import pip_install

        spec = _make_spec()  # dep_hashes={}
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            rc = pip_install("my-plugin==1.0", spec)

        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--no-deps" in cmd
        assert "--require-hashes" not in cmd
        assert "my-plugin==1.0" in cmd

    def test_no_spec_adds_no_deps_flag(self):
        """pip_install with spec=None must also pass --no-deps."""
        from intentumdiff.plugins.hub import pip_install

        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            pip_install("my-plugin==1.0", None)

        cmd = mock_run.call_args[0][0]
        assert "--no-deps" in cmd

    def test_with_dep_hashes_uses_require_hashes(self, tmp_path):
        """pip_install with non-empty dep_hashes must write a requirements file
        and pass --require-hashes -r <file> to pip."""
        from intentumdiff.plugins.hub import pip_install

        spec = _make_spec(
            allowed_dependencies=["dep-a"],
            dep_hashes={
                "my-plugin==1.0": _hash("a"),
                "dep-a==2.0": _hash("b"),
            }
        )

        import intentumdiff.plugins.hub as _hub

        def fake_download(req_file, dest):
            _write_clean_wheelhouse(
                Path(dest),
                "my_plugin-1.0-py3-none-any.whl",
                "dep_a-2.0-py3-none-any.whl",
            )
            return 0

        with (
            patch.object(_hub, "pip_download_requirements", side_effect=fake_download),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            rc = pip_install("my-plugin==1.0", spec)

        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--require-hashes" in cmd
        assert "-r" in cmd
        assert "--no-deps" in cmd
        assert "--no-index" in cmd
        assert "--find-links" in cmd

    def test_dep_hashes_requirements_file_contents(self, tmp_path):
        """The generated requirements file must contain every dep_hashes entry."""
        from intentumdiff.plugins.hub import pip_install

        hashes = {"mypkg==1.0": _hash("d"), "other==2.0": _hash("e")}
        spec = _make_spec(dep_hashes=hashes, allowed_dependencies=["other"])

        written_content: list[str] = []

        import intentumdiff.plugins.hub as _hub

        def fake_download(req_file, dest):
            written_content.append(Path(req_file).read_text(encoding="utf-8"))
            _write_clean_wheelhouse(
                Path(dest),
                "mypkg-1.0-py3-none-any.whl",
                "other-2.0-py3-none-any.whl",
            )
            return 0

        with (
            patch.object(_hub, "pip_download_requirements", side_effect=fake_download),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            pip_install("mypkg==1.0", spec)

        assert written_content, "Requirements file was never read"
        content = written_content[0]
        assert "mypkg==1.0" in content
        assert "other==2.0" in content
        assert f"--hash={_hash('d')}" in content
        assert f"--hash={_hash('e')}" in content

    def test_temp_requirements_file_deleted_after_install(self, tmp_path):
        """The temporary requirements file must be deleted even on pip success."""
        from intentumdiff.plugins.hub import pip_install

        spec = _make_spec(dep_hashes={"x==1": _hash("a")})
        req_paths: list[Path] = []

        import intentumdiff.plugins.hub as _hub

        def fake_download(req_file, dest):
            req_paths.append(Path(req_file))
            _write_clean_wheelhouse(Path(dest), "x-1-py3-none-any.whl")
            return 0

        with (
            patch.object(_hub, "pip_download_requirements", side_effect=fake_download),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            pip_install("x==1", spec)

        assert req_paths, "Expected the requirements file path to be captured"
        assert not req_paths[0].exists(), "Temp requirements file was not cleaned up"

    def test_dep_hashes_dependency_wheel_lint_blocks_install(self, tmp_path):
        from intentumdiff.plugins.hub import pip_install

        spec = _make_spec(
            allowed_dependencies=["dep-a"],
            dep_hashes={
                "my-plugin==1.0": _hash("a"),
                "dep-a==2.0": _hash("b"),
            },
        )

        import intentumdiff.plugins.hub as _hub

        def fake_download(req_file, dest):
            dest = Path(dest)
            _build_wheel(
                dest,
                {"pkg/__init__.py": b""},
                filename="my_plugin-1.0-py3-none-any.whl",
            )
            _build_wheel(
                dest,
                {"dep/__init__.py": b"", "evil.pth": b"/tmp/evil"},
                filename="dep_a-2.0-py3-none-any.whl",
            )
            return 0

        with (
            patch.object(_hub, "pip_download_requirements", side_effect=fake_download),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
            pytest.raises(ValueError, match=".pth"),
        ):
            pip_install("my-plugin==1.0", spec)

        mock_run.assert_not_called()


class TestInstallTargetClassification:
    def test_pep508_direct_url_is_classified_as_direct_url(self) -> None:
        from intentumdiff.plugins.hub import classify_install_target

        assert (
            classify_install_target(
                "intentumdiff-plugin @ https://example.test/intentumdiff-plugin.whl"
            )
            == "direct_url"
        )


# ---------------------------------------------------------------------------
# _lint_wheel_contents
# ---------------------------------------------------------------------------


class TestLintWheelContents:
    def test_clean_wheel_returns_no_errors(self, tmp_path):
        """A wheel containing only .py files and a .wasm must be accepted."""
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "myplugin/parser.wasm": b"\x00asm",
        })
        errors = _lint_wheel_contents(wheel)
        assert errors == []

    def test_pth_file_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "evil.pth": b"/evil/path",
        })
        errors = _lint_wheel_contents(wheel)
        assert any(".pth" in e for e in errors), f"Expected .pth error, got: {errors}"

    def test_archive_traversal_path_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            r"..\evil.py": b"",
        })
        errors = _lint_wheel_contents(wheel)
        assert any("unsafe archive path" in e for e in errors), errors

    def test_sitecustomize_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "sitecustomize.py": b"import os; os.system('evil')",
        })
        errors = _lint_wheel_contents(wheel)
        assert any("sitecustomize" in e for e in errors), (
            f"Expected sitecustomize error, got: {errors}"
        )

    def test_usercustomize_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "usercustomize.py": b"pass",
        })
        errors = _lint_wheel_contents(wheel)
        assert any("usercustomize" in e for e in errors), (
            f"Expected usercustomize error, got: {errors}"
        )

    def test_pyd_extension_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "myplugin/_fast.pyd": b"\x00native",
        })
        errors = _lint_wheel_contents(wheel)
        assert any(".pyd" in e for e in errors), f"Expected .pyd error, got: {errors}"

    def test_so_extension_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "myplugin/_fast.so": b"\x7fELF",
        })
        errors = _lint_wheel_contents(wheel)
        assert any(".so" in e for e in errors), f"Expected .so error, got: {errors}"

    def test_dll_extension_is_rejected(self, tmp_path):
        from intentumdiff.plugins.hub import _lint_wheel_contents

        wheel = _build_wheel(tmp_path, {
            "myplugin/__init__.py": b"",
            "myplugin/_fast.dll": b"MZ",
        })
        errors = _lint_wheel_contents(wheel)
        assert any(".dll" in e for e in errors), f"Expected .dll error, got: {errors}"

    def test_bad_zip_returns_error(self, tmp_path):
        """A file that is not a valid ZIP must produce an error, not raise."""
        from intentumdiff.plugins.hub import _lint_wheel_contents

        bad = tmp_path / "bad.whl"
        bad.write_bytes(b"not a zip")
        errors = _lint_wheel_contents(bad)
        assert errors, "Expected an error for a corrupt wheel"


# ---------------------------------------------------------------------------
# pre_install_security_check: fail-closed for missing .wasm
# ---------------------------------------------------------------------------


class TestPreInstallSecurityCheck:
    def _make_wheel_without_wasm(self, tmp_path: Path) -> Path:
        return _build_wheel(tmp_path, {"myplugin/__init__.py": b""})

    def test_missing_wasm_with_expected_checksums_is_error(self, tmp_path):
        """When spec.wasm_checksums is non-empty but the wheel has no .wasm,
        pre_install_security_check must produce an error (not a warning)."""
        from intentumdiff.plugins.hub import pre_install_security_check

        spec = _make_spec(
            source="pypi",
            ref="1.0",
            wasm_checksums={"parser.wasm": "a" * 64},
        )
        wheel = self._make_wheel_without_wasm(tmp_path)

        # Patch pip_download to succeed and write nothing (no wheel downloaded),
        # then make Path.glob return our pre-built wheel.
        import intentumdiff.plugins.hub as _hub
        import subprocess

        def fake_pip_download(target, dest, **kw):
            # Copy our wheel into dest so pre_install_security_check finds it.
            import shutil
            shutil.copy(str(wheel), dest)
            return 0

        with patch.object(_hub, "pip_download", side_effect=fake_pip_download):
            errors, warnings = pre_install_security_check(spec.install_target, spec)

        assert errors, "Expected an error when spec expects .wasm but wheel has none"
        assert any(
            "parser.wasm" in e or "checksum" in e.lower() or ".wasm" in e.lower()
            for e in errors
        ), f"Error should mention the missing wasm; errors={errors}"

    def test_missing_wasm_without_expected_checksums_is_warning_only(self, tmp_path):
        """When spec.wasm_checksums is empty and wheel has no .wasm,
        we warn but do not error (TOFU install path)."""
        from intentumdiff.plugins.hub import pre_install_security_check

        spec = _make_spec(source="pypi", ref="1.0", wasm_checksums={})
        wheel = self._make_wheel_without_wasm(tmp_path)

        import intentumdiff.plugins.hub as _hub

        def fake_pip_download(target, dest, **kw):
            import shutil
            shutil.copy(str(wheel), dest)
            return 0

        with patch.object(_hub, "pip_download", side_effect=fake_pip_download):
            errors, warnings = pre_install_security_check(spec.install_target, spec)

        assert not errors, f"Expected no error for TOFU install, got: {errors}"
        assert warnings, "Expected a warning that .wasm verification was skipped"

    def test_dep_hashes_lints_every_dependency_wheel(self, tmp_path):
        from intentumdiff.plugins.hub import pre_install_security_check

        spec = _make_spec(
            name="my-plugin",
            source="pypi",
            ref="1.0",
            allowed_dependencies=["dep-a"],
            dep_hashes={
                "intentumdiff-my-plugin==1.0": _hash("a"),
                "dep-a==2.0": _hash("b"),
            },
        )

        import intentumdiff.plugins.hub as _hub

        def fake_download(req_file, dest):
            dest = Path(dest)
            _build_wheel(
                dest,
                {"pkg/parser.wasm": b"\x00asm"},
                filename="intentumdiff_my_plugin-1.0-py3-none-any.whl",
            )
            _build_wheel(
                dest,
                {"dep/__init__.py": b"", "evil.pth": b"/tmp/evil"},
                filename="dep_a-2.0-py3-none-any.whl",
            )
            return 0

        with patch.object(_hub, "pip_download_requirements", side_effect=fake_download):
            errors, warnings = pre_install_security_check(spec.install_target, spec)

        assert any(".pth" in error for error in errors)


# ---------------------------------------------------------------------------
# PluginSpec dep_hashes round-trip through load/save YAML
# ---------------------------------------------------------------------------


class TestPluginSpecDepHashesYaml:
    def test_dep_hashes_round_trip(self, tmp_path):
        """dep_hashes must survive a save → load cycle."""
        pytest.importorskip("yaml", reason="pyyaml not installed")
        from intentumdiff.plugins.hub import PluginSpec, load_plugins_file, save_plugins_file

        spec = PluginSpec(
            name="myplugin",
            source="pypi",
            ref="1.0.0",
            allowed_dependencies=["dep"],
            dep_hashes={
                "myplugin==1.0.0": _hash("a"),
                "dep==2.0": _hash("b"),
            },
        )
        plugins_file = tmp_path / "intentumdiff_plugins.yaml"
        save_plugins_file(plugins_file, [spec])
        loaded = load_plugins_file(plugins_file)

        assert len(loaded) == 1
        loaded_spec = loaded[0]
        assert loaded_spec.dep_hashes == spec.dep_hashes
        assert loaded_spec.allowed_dependencies == spec.allowed_dependencies

    def test_missing_dep_hashes_key_defaults_to_empty(self, tmp_path):
        """A YAML entry without dep_hashes must deserialize to dep_hashes={}."""
        pytest.importorskip("yaml", reason="pyyaml not installed")
        from intentumdiff.plugins.hub import load_plugins_file

        plugins_file = tmp_path / "intentumdiff_plugins.yaml"
        plugins_file.write_text(
            "version: 1\nplugins:\n  - name: test\n    source: pypi\n    ref: '1.0'\n",
            encoding="utf-8",
        )
        loaded = load_plugins_file(plugins_file)
        assert loaded[0].dep_hashes == {}


# ---------------------------------------------------------------------------
# _validate_registry_ref
# ---------------------------------------------------------------------------


class TestValidateRegistryRef:
    """_validate_registry_ref must accept safe refs and reject path traversal."""

    def test_accepts_main(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        _validate_registry_ref("main")  # must not raise

    def test_accepts_full_sha(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        _validate_registry_ref("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")  # 40-char SHA

    def test_accepts_tag_with_dot(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        _validate_registry_ref("v1.2.3")

    def test_rejects_dotdot_traversal(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        with pytest.raises(ValueError):
            _validate_registry_ref("../evil")

    def test_rejects_empty_string(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        with pytest.raises(ValueError):
            _validate_registry_ref("")

    def test_rejects_leading_dot(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        with pytest.raises(ValueError):
            _validate_registry_ref(".hidden")

    def test_rejects_overlong_ref(self):
        from intentumdiff.plugins.hub import _validate_registry_ref
        with pytest.raises(ValueError):
            _validate_registry_ref("a" * 201)
