"""VCS host I/O must preserve real working-tree UTF-8, never invent source."""
from __future__ import annotations

from pathlib import Path

import pytest

from intentumdiff.vcs.git_backend import GitVcsBackend
from intentumdiff.vcs.hg_backend import HgVcsBackend
from intentumdiff.vcs.perforce_backend import PerforceVcsBackend
from intentumdiff.vcs.svn_backend import SvnVcsBackend


@pytest.fixture(params=[
    pytest.param(GitVcsBackend, id="git"),
    pytest.param(HgVcsBackend, id="hg"),
    pytest.param(SvnVcsBackend, id="svn"),
    pytest.param(PerforceVcsBackend, id="p4"),
])
def working_backend(request: pytest.FixtureRequest, tmp_path: Path):
    """Bypass only CLI root discovery; exercise the real file-reading method."""
    backend_cls = request.param
    backend = backend_cls.__new__(backend_cls)
    backend._root = str(tmp_path)
    return backend


@pytest.mark.parametrize("invalid_byte", [b"\xff", b"\xfe"], ids=["ff", "fe"])
def test_working_file_invalid_utf8_fails_closed(
    working_backend, tmp_path: Path, invalid_byte: bytes
) -> None:
    # Distinct bytes must not collapse into the same replacement-character source.
    (tmp_path / "fixture.py").write_bytes(b"x = " + invalid_byte + b"\n")
    with pytest.raises(UnicodeDecodeError):
        working_backend.get_working_file("fixture.py")


def test_working_file_preserves_valid_unicode(working_backend, tmp_path: Path) -> None:
    source = "name = 'é🐘'\n"
    (tmp_path / "fixture.py").write_bytes(source.encode("utf-8"))
    assert working_backend.get_working_file("fixture.py") == source


def test_working_file_missing_is_documented_empty(working_backend) -> None:
    assert working_backend.get_working_file("missing.py") == ""
