"""Host-I/O completeness regressions for plain directory indexing (#203)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from intentumdiff.core.indexer import Indexer


def test_directory_read_failure_never_returns_a_partial_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("answer = 42\n", encoding="utf-8")
    (tmp_path / "blocked.py").write_text("changed = True\n", encoding="utf-8")
    # The host-I/O implementation reads raw bytes before invoking Rust's
    # content classifier. Intercept that actual boundary, not Path.read_text.
    original_read_bytes = Path.read_bytes

    def controlled_read(path: Path, *args: object, **kwargs: object) -> bytes:
        if path.name == "blocked.py":
            raise PermissionError("controlled read denial")
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", controlled_read)
    differ = MagicMock()
    indexer = Indexer(differ)
    with (
        patch("intentumdiff.core.indexer.load_diffignore", return_value=None),
        patch.object(indexer, "_index_files") as build_index,
        pytest.raises(RuntimeError, match=r"blocked\.py.*controlled read denial") as failure,
    ):
        indexer.index_directory(tmp_path)

    assert isinstance(failure.value.__cause__, PermissionError)
    build_index.assert_not_called()
    differ.parse.assert_not_called()


def test_directory_readable_and_ignored_files_remain_distinct(
    tmp_path: Path,
) -> None:
    (tmp_path / "included.py").write_bytes(b"x = 1\n")
    (tmp_path / "ignored.py").write_bytes(b"y = 2\n")
    differ = MagicMock()
    indexer = Indexer(differ)
    completed = SimpleNamespace(files_ignored=0, ignored_files=[])
    ignore = SimpleNamespace(is_ignored=lambda path: path == "ignored.py")
    with (
        patch("intentumdiff.core.indexer.load_diffignore", return_value=ignore),
        patch.object(indexer, "_index_files", return_value=completed) as build_index,
    ):
        result = indexer.index_directory(tmp_path)

    assert result is completed
    assert result.files_ignored == 1
    assert result.ignored_files == ["ignored.py"]
    build_index.assert_called_once()
    assert build_index.call_args.args[0] == [("included.py", "x = 1\n")]
    differ.parse.assert_not_called()


def test_directory_invalid_utf8_never_becomes_partial_success(tmp_path: Path) -> None:
    """Malformed source cannot be indexed as replacement-character content."""
    (tmp_path / "good.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_bytes(b"value = \xff\n")
    indexer = Indexer(MagicMock())
    with (
        patch("intentumdiff.core.indexer.load_diffignore", return_value=None),
        patch.object(indexer, "_index_files") as build_index,
        pytest.raises(RuntimeError, match=r"bad\.py") as failure,
    ):
        indexer.index_directory(tmp_path)
    assert isinstance(failure.value.__cause__, UnicodeDecodeError)
    build_index.assert_not_called()
