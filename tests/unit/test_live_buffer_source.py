"""Unit tests for LiveBufferSource."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_absolute_path_rejected(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        with pytest.raises(ValueError, match="Unsafe"):
            LiveBufferSource("/repo", "/etc/passwd", "content")

    def test_parent_traversal_rejected(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        with pytest.raises(ValueError, match="Unsafe"):
            LiveBufferSource("/repo", "../secret.py", "content")

    def test_nested_traversal_rejected(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        with pytest.raises(ValueError, match="Unsafe"):
            LiveBufferSource("/repo", "src/../../../etc/passwd", "content")

    def test_windows_traversal_rejected(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        with pytest.raises(ValueError, match="Unsafe"):
            LiveBufferSource("/repo", r"src\..\secret.py", "content")

    def test_windows_drive_path_rejected(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        with pytest.raises(ValueError, match="Unsafe"):
            LiveBufferSource("/repo", "C:/Windows/win.ini", "content")

    def test_valid_relative_path_accepted(self) -> None:
        """Should not raise for a normal relative path."""
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", "src/main.py", "content")
        assert src._file_path == "src/main.py"

    def test_nested_relative_path_accepted(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", "a/b/c.py", "x=1")
        assert src._file_path == "a/b/c.py"


# ---------------------------------------------------------------------------
# get_content
# ---------------------------------------------------------------------------


class TestGetContent:
    def _source(self, file_path: str, live_content: str, committed: str, **kw):
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", file_path, live_content, **kw)
        # _read_committed shells to git (#98); mock the git_cli helpers so the
        # committed side returns our content.
        with (
            patch(
                "intentdiff.sources.live_buffer_source.resolve_repo_root",
                return_value="/repo",
            ),
            patch(
                "intentdiff.sources.live_buffer_source.run_git_bytes",
                return_value=committed.encode("utf-8"),
            ),
        ):
            return src.get_content()

    def test_returns_four_tuple(self) -> None:
        result = self._source("src/main.py", "new_content", "old_content")
        assert len(result) == 4

    def test_old_content_from_git(self) -> None:
        old, _, _, _ = self._source("src/main.py", "new", "old_from_git")
        assert old == "old_from_git"

    def test_new_content_is_live_buffer(self) -> None:
        _, new, _, _ = self._source("src/main.py", "live_content", "committed")
        assert new == "live_content"

    def test_filename_is_basename(self) -> None:
        _, _, filename, _ = self._source("src/main.py", "x", "y")
        assert filename == "main.py"

    def test_language_hint_none_by_default(self) -> None:
        _, _, _, hint = self._source("src/main.py", "x", "y")
        assert hint is None

    def test_language_hint_propagated(self) -> None:
        _, _, _, hint = self._source("src/main.py", "x", "y", language_hint="python")
        assert hint == "python"

    def test_ref_default_is_head(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", "foo.py", "content")
        assert src._ref == "HEAD"

    def test_custom_ref(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", "foo.py", "content", ref="main")
        assert src._ref == "main"

    def test_untracked_file_returns_empty_old(self) -> None:
        """When the file isn't in the git tree, old content is '' (empty string)."""
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", "new_file.py", "new_content")
        with (
            patch(
                "intentdiff.sources.live_buffer_source.resolve_repo_root",
                return_value="/repo",
            ),
            patch(
                "intentdiff.sources.live_buffer_source.run_git_bytes",
                side_effect=subprocess.CalledProcessError(1, "git"),
            ),
        ):
            old, new, _, _ = src.get_content()
        assert old == ""
        assert new == "new_content"


# ---------------------------------------------------------------------------
# EditDelta passthrough
# ---------------------------------------------------------------------------


class TestEditDeltas:
    def test_edit_deltas_stored(self) -> None:
        from intentdiff.core.models import EditDelta
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        deltas = [
            EditDelta(
                start_byte=0,
                old_end_byte=3,
                new_end_byte=5,
                start_point=(0, 0),
                old_end_point=(0, 3),
                new_end_point=(0, 5),
            )
        ]
        src = LiveBufferSource("/repo", "foo.py", "content", edit_deltas=deltas)
        assert src.edit_deltas == deltas

    def test_no_deltas_by_default(self) -> None:
        from intentdiff.sources.live_buffer_source import LiveBufferSource

        src = LiveBufferSource("/repo", "foo.py", "content")
        assert src.edit_deltas is None
