from __future__ import annotations

from pathlib import Path

import pytest

from scripts.set_release_version import set_project_version


def test_set_project_version_updates_only_first_project_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.before]\n"
        'version = "leave-alone-too"\n\n'
        "[project]\n"
        'name = "intentumdiff"\n'
        'version = "0.0.1b1"\n\n'
        "[tool.other]\n"
        'version = "leave-alone"\n',
        encoding="utf-8",
    )

    set_project_version(pyproject, "0.0.1b2")

    assert pyproject.read_text(encoding="utf-8") == (
        "[tool.before]\n"
        'version = "leave-alone-too"\n\n'
        "[project]\n"
        'name = "intentumdiff"\n'
        'version = "0.0.1b2"\n\n'
        "[tool.other]\n"
        'version = "leave-alone"\n'
    )


@pytest.mark.parametrize("version", ["0.1", "0.0.1-beta.1", "0.0.1 beta1", "../0.0.1"])
def test_set_project_version_rejects_unsupported_versions(
    tmp_path: Path,
    version: str,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('version = "0.0.1b1"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported release version"):
        set_project_version(pyproject, version)
