"""Unit tests for intentumdiff.watcher."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from intentumdiff.watcher import FileWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ImmediateTimer:
    """threading.Timer substitute: fires the callback synchronously on start()."""

    def __init__(self, delay: float, fn, args: tuple = ()) -> None:
        self._fn = fn
        self._args = args
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def start(self) -> None:
        if not self._cancelled:
            self._fn(*self._args)


class _LazyTimer:
    """threading.Timer substitute: start() does NOT fire — used to test cancellation."""

    def __init__(self, delay: float, fn, args: tuple = ()) -> None:
        self._fn = fn
        self._args = args
        self.started = False
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def start(self) -> None:
        self.started = True


def _make_file_event(path: str, *, is_directory: bool = False) -> MagicMock:
    ev = MagicMock()
    ev.src_path = path
    ev.dest_path = path
    ev.is_directory = is_directory
    return ev


def _make_moved_event(src: str, dest: str) -> MagicMock:
    ev = MagicMock()
    ev.src_path = src
    ev.dest_path = dest
    ev.is_directory = False
    return ev


def _watcher(tmp_path: Path, **kw) -> "FileWatcher":
    """Create a FileWatcher for testing with a no-op render_fn."""
    from intentumdiff.watcher import FileWatcher

    differ = MagicMock()
    return FileWatcher([tmp_path], differ, render_fn=MagicMock(), **kw)


# ---------------------------------------------------------------------------
# Debounce behaviour
# ---------------------------------------------------------------------------


class TestDebounce:
    def test_coalesces_rapid_events_for_same_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Three rapid on_modified events for the same path → only one timer survives."""
        # Use _LazyTimer so timers don't auto-fire; we manually invoke the survivor.
        monkeypatch.setattr("intentumdiff.watcher.threading.Timer", _LazyTimer)

        watcher = _watcher(tmp_path)
        calls: list[str] = []
        watcher._run_diff = lambda p: calls.append(p)

        path = str(tmp_path / "foo.py")
        watcher._schedule_diff(path)
        watcher._schedule_diff(path)
        watcher._schedule_diff(path)

        # Exactly one pending (uncancelled) timer should remain
        assert len(watcher._timers) == 1
        survivor = list(watcher._timers.values())[0]
        assert not survivor.cancelled

        # Fire it: _run_diff should be called exactly once
        survivor._fn(*survivor._args)
        assert calls == [path]

    def test_separate_timer_per_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Events for two different paths produce two _run_diff calls."""
        monkeypatch.setattr("intentumdiff.watcher.threading.Timer", _ImmediateTimer)

        watcher = _watcher(tmp_path)
        calls: list[str] = []
        watcher._run_diff = lambda p: calls.append(p)

        path_a = str(tmp_path / "a.py")
        path_b = str(tmp_path / "b.py")
        watcher._schedule_diff(path_a)
        watcher._schedule_diff(path_b)

        assert sorted(calls) == sorted([path_a, path_b])

    def test_stop_cancels_pending_timers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """stop() cancels all pending debounce timers before stopping the observer."""
        timers: list[_LazyTimer] = []

        def _tracking_timer(delay, fn, args=()):
            t = _LazyTimer(delay, fn, args)
            timers.append(t)
            return t

        monkeypatch.setattr("intentumdiff.watcher.threading.Timer", _tracking_timer)

        watcher = _watcher(tmp_path)
        watcher._run_diff = lambda p: None  # don't actually diff
        watcher._observer = MagicMock()  # don't start real observer

        watcher._schedule_diff(str(tmp_path / "a.py"))
        watcher._schedule_diff(str(tmp_path / "b.py"))

        assert len(watcher._timers) == 2

        watcher.stop()

        assert all(t.cancelled for t in timers)
        assert len(watcher._timers) == 0


# ---------------------------------------------------------------------------
# Event handler filtering
# ---------------------------------------------------------------------------


class TestEventHandlerFiltering:
    def test_tilde_backup_ignored(self, tmp_path: Path) -> None:
        """Files ending with ~ are not scheduled for diff."""
        watcher = _watcher(tmp_path)
        scheduled: list[str] = []
        watcher._schedule_diff = lambda p: scheduled.append(p)

        watcher._handler.on_modified(_make_file_event(str(tmp_path / "foo.py~")))

        assert scheduled == []

    def test_swp_ignored(self, tmp_path: Path) -> None:
        """Vim .swp files are not scheduled."""
        watcher = _watcher(tmp_path)
        scheduled: list[str] = []
        watcher._schedule_diff = lambda p: scheduled.append(p)

        watcher._handler.on_modified(_make_file_event(str(tmp_path / ".foo.swp")))

        assert scheduled == []

    def test_tmp_ignored(self, tmp_path: Path) -> None:
        """.tmp files are not scheduled."""
        watcher = _watcher(tmp_path)
        scheduled: list[str] = []
        watcher._schedule_diff = lambda p: scheduled.append(p)

        watcher._handler.on_modified(_make_file_event(str(tmp_path / "write.tmp")))

        assert scheduled == []

    def test_directory_event_ignored(self, tmp_path: Path) -> None:
        """Directory modification events are not scheduled."""
        watcher = _watcher(tmp_path)
        scheduled: list[str] = []
        watcher._schedule_diff = lambda p: scheduled.append(p)

        watcher._handler.on_modified(_make_file_event(str(tmp_path), is_directory=True))

        assert scheduled == []

    def test_on_created_schedules_normal_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """on_created triggers a diff (handles atomic-rename saves)."""
        monkeypatch.setattr("intentumdiff.watcher.threading.Timer", _ImmediateTimer)

        watcher = _watcher(tmp_path)
        calls: list[str] = []
        watcher._run_diff = lambda p: calls.append(p)

        path = str(tmp_path / "app.ts")
        watcher._handler.on_created(_make_file_event(path))

        assert calls == [path]

    def test_on_moved_schedules_dest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """on_moved uses dest_path (rename-into-place pattern)."""
        monkeypatch.setattr("intentumdiff.watcher.threading.Timer", _ImmediateTimer)

        watcher = _watcher(tmp_path)
        calls: list[str] = []
        watcher._run_diff = lambda p: calls.append(p)

        dest = str(tmp_path / "real_file.py")
        ev = _make_moved_event(str(tmp_path / ".real_file.py.tmp"), dest)
        watcher._handler.on_moved(ev)

        assert calls == [dest]

    def test_on_moved_ignores_temp_dest(self, tmp_path: Path) -> None:
        """on_moved does not schedule if the destination is itself a temp file."""
        watcher = _watcher(tmp_path)
        scheduled: list[str] = []
        watcher._schedule_diff = lambda p: scheduled.append(p)

        ev = _make_moved_event(str(tmp_path / "a.py"), str(tmp_path / "a.py~"))
        watcher._handler.on_moved(ev)

        assert scheduled == []


# ---------------------------------------------------------------------------
# _run_diff exception handling
# ---------------------------------------------------------------------------


class TestRunDiffExceptions:
    def _patched_watcher(self, tmp_path: Path) -> tuple["FileWatcher", list[str]]:
        """Return (watcher, warnings) where warnings captures _warn.print calls."""
        from intentumdiff.watcher import FileWatcher

        differ = MagicMock()
        watcher = FileWatcher([tmp_path], differ, render_fn=MagicMock())
        warnings: list[str] = []
        watcher._warn = MagicMock()
        watcher._warn.print = lambda msg, **kw: warnings.append(str(msg))
        return watcher, warnings

    def _mock_repo(self, tmp_path: Path) -> MagicMock:
        mock_repo = MagicMock()
        mock_repo.working_dir = str(tmp_path)
        return mock_repo

    def test_untracked_file_prints_warning(self, tmp_path: Path) -> None:
        """KeyError from differ.diff produces a yellow untracked warning, no crash."""
        watcher, warnings = self._patched_watcher(tmp_path)
        watcher._differ.diff.side_effect = KeyError("blob not found")

        with (
            patch("intentumdiff.watcher.resolve_repo_root") as mock_resolve,
            patch("intentumdiff.watcher.WorkingTreeSource"),
        ):
            mock_resolve.return_value = str(tmp_path)
            watcher._run_diff(str(tmp_path / "new_file.py"))

        assert any("untracked" in w for w in warnings)

    def test_plugin_not_found_is_silent(self, tmp_path: Path) -> None:
        """PluginNotFoundError is silently swallowed — no warnings, no crash."""
        from intentumdiff.plugins.exceptions import PluginNotFoundError

        watcher, warnings = self._patched_watcher(tmp_path)
        watcher._differ.diff.side_effect = PluginNotFoundError("no parser for .toml")

        with (
            patch("intentumdiff.watcher.resolve_repo_root") as mock_resolve,
            patch("intentumdiff.watcher.WorkingTreeSource"),
        ):
            mock_resolve.return_value = str(tmp_path)
            watcher._run_diff(str(tmp_path / "config.toml"))

        assert warnings == []

    def test_file_not_found_is_silent(self, tmp_path: Path) -> None:
        """FileNotFoundError (deleted after event) is silently ignored."""
        watcher, warnings = self._patched_watcher(tmp_path)
        watcher._differ.diff.side_effect = FileNotFoundError("gone")

        with (
            patch("intentumdiff.watcher.resolve_repo_root") as mock_resolve,
            patch("intentumdiff.watcher.WorkingTreeSource"),
        ):
            mock_resolve.return_value = str(tmp_path)
            watcher._run_diff(str(tmp_path / "gone.py"))

        assert warnings == []

    def test_invalid_git_repo_prints_warning(self, tmp_path: Path) -> None:
        """NotAGitRepositoryError prints a warning and does not crash."""
        from intentumdiff.vcs.git_cli import NotAGitRepositoryError

        watcher, warnings = self._patched_watcher(tmp_path)

        with patch("intentumdiff.watcher.resolve_repo_root") as mock_resolve:
            mock_resolve.side_effect = NotAGitRepositoryError("not a repo")
            watcher._run_diff(str(tmp_path / "foo.py"))

        assert any("git repository" in w.lower() for w in warnings)

    def test_unexpected_error_prints_red(self, tmp_path: Path) -> None:
        """Unexpected exceptions print an error line and keep the watcher running."""
        watcher, warnings = self._patched_watcher(tmp_path)
        watcher._differ.diff.side_effect = RuntimeError("unexpected boom")

        with (
            patch("intentumdiff.watcher.resolve_repo_root") as mock_resolve,
            patch("intentumdiff.watcher.WorkingTreeSource"),
        ):
            mock_resolve.return_value = str(tmp_path)
            watcher._run_diff(str(tmp_path / "boom.py"))

        assert any("Error" in w or "boom" in w for w in warnings)
