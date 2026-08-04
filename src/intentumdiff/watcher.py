"""intentumdiff.watcher
~~~~~~~~~~~~~~~~~~~~~~~~~~

File-system watcher for ``intentumdiff watch``.  Watches a set of files/directories
for modifications, debounces rapid editor saves, and runs a semantic diff
against the current HEAD for every changed file.

Requires the ``watchdog`` package, which is a core IntentumDiff dependency.
"""

from __future__ import annotations

import threading
import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from intentumdiff.plugins.exceptions import PluginNotFoundError
from intentumdiff.sources.git_source import WorkingTreeSource
from intentumdiff.vcs.git_cli import NotAGitRepositoryError, resolve_repo_root

if TYPE_CHECKING:
    from intentumdiff.core.models import SemanticDiff
    from intentumdiff.differ import SemanticDiffer
    from rich.console import Console as RichConsole


# ---------------------------------------------------------------------------
# Temp-file filter
# ---------------------------------------------------------------------------

_IGNORED_SUFFIXES = frozenset({".swp", ".swx", ".tmp", ".bak", ".orig", ".pyc"})
_IGNORED_BASENAME_SUFFIXES = ("~",)


def _is_temp_file(path: str) -> bool:
    """Return True for editor swap/backup/temp files that should not trigger a diff."""
    p = Path(path)
    if p.suffix.lower() in _IGNORED_SUFFIXES:
        return True
    if p.name.endswith(_IGNORED_BASENAME_SUFFIXES):
        return True
    return False


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------


class _WatchEventHandler(FileSystemEventHandler):
    """Filters transient files and dispatches modified/created paths to FileWatcher."""

    def __init__(self, watcher: FileWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = str(event.src_path)
        if _is_temp_file(path):
            return
        self._watcher._schedule_diff(path)

    def on_created(self, event: FileSystemEvent) -> None:
        """Handles atomic-rename saves (VS Code on Windows, vim 'writebackup')."""
        if event.is_directory:
            return
        path = str(event.src_path)
        if _is_temp_file(path):
            return
        self._watcher._schedule_diff(path)

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handles rename-into-place saves (common on Windows with ReadDirectoryChangesW)."""
        if event.is_directory:
            return
        dest = str(event.dest_path)
        if not _is_temp_file(dest):
            self._watcher._schedule_diff(dest)


# ---------------------------------------------------------------------------
# FileWatcher
# ---------------------------------------------------------------------------


class FileWatcher:
    """Watch files/directories and emit semantic diffs when they are saved.

    Parameters
    ----------
    paths:
        Files or directories to watch.  Directories are watched recursively.
    differ:
        A configured :class:`~intentumdiff.differ.SemanticDiffer` instance
        that is reused for every diff.
    render_fn:
        Callable that receives a :class:`~intentumdiff.core.models.SemanticDiff`
        and renders it to the user.  Called from a background thread — the
        callable must be thread-safe (Rich Console satisfies this).
    ref:
        Git ref to compare the working tree against (default: ``"HEAD"``).
    debounce:
        Seconds to wait after the last file-system event before running the diff
        (default: 0.3 s).  Coalesces rapid editor saves for the same file.
    console:
        Rich ``Console`` used for status and warning messages.  Defaults to a
        new stderr console if not provided.
    """

    def __init__(
        self,
        paths: list[str | Path],
        differ: SemanticDiffer,
        *,
        render_fn: Callable[[SemanticDiff], None],
        ref: str = "HEAD",
        debounce: float = 0.3,
        console: RichConsole | None = None,
    ) -> None:
        from rich.console import Console

        self._paths = [Path(p).resolve() for p in paths]
        self._differ = differ
        self._render_fn = render_fn
        self._ref = ref
        self._debounce = debounce
        # _warn goes to stderr; _out goes to stdout for diff output
        self._warn: RichConsole = console or Console(stderr=True, highlight=False)
        self._out: RichConsole = Console()
        self._observer: Observer = Observer()
        self._handler = _WatchEventHandler(self)
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Register watches and start the observer thread."""
        watch_count = 0
        for p in self._paths:
            if not p.exists():
                self._warn.print(
                    f"[yellow]Warning: path does not exist and will be skipped: {p}[/yellow]"
                )
                continue
            recursive = p.is_dir()
            self._observer.schedule(self._handler, str(p), recursive=recursive)
            watch_count += 1

        if watch_count == 0:
            raise ValueError("No valid paths to watch — all supplied paths were missing.")

        self._observer.start()

    def stop(self) -> None:
        """Cancel all pending debounce timers and stop the observer thread."""
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()
        self._observer.stop()
        self._observer.join()

    def wait(self) -> None:
        """Block the calling thread until the observer stops (or KeyboardInterrupt)."""
        self._observer.join()

    # ------------------------------------------------------------------
    # Internal: debounce scheduling
    # ------------------------------------------------------------------

    def _schedule_diff(self, path: str) -> None:
        """Schedule a diff for *path*, cancelling any existing pending timer first."""
        with self._lock:
            existing = self._timers.pop(path, None)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(self._debounce, self._run_diff, args=(path,))
            self._timers[path] = t
            t.start()

    # ------------------------------------------------------------------
    # Internal: diff execution
    # ------------------------------------------------------------------

    def _run_diff(self, path: str) -> None:
        """Resolve the git repo context, run the diff, and render the result."""
        # Clean up our own timer entry
        with self._lock:
            self._timers.pop(path, None)

        abs_path = Path(path).resolve()

        # ── Resolve git repo ──────────────────────────────────────────────
        try:
            repo_root = Path(resolve_repo_root(abs_path.parent)).resolve()
        except NotAGitRepositoryError:
            self._warn.print(
                f"[yellow]Warning: {path!r} is not inside a git repository — skipping[/yellow]"
            )
            return
        try:
            rel_path = abs_path.relative_to(repo_root)
        except ValueError:
            self._warn.print(
                f"[yellow]Warning: cannot resolve {abs_path} relative to "
                f"repo root {repo_root} — skipping[/yellow]"
            )
            return

        rel_path_str = str(rel_path)

        # ── Run the diff ──────────────────────────────────────────────────
        try:
            source = WorkingTreeSource(
                repo_path=str(repo_root),
                file_path=rel_path_str,
                ref=self._ref,
            )
            diff = self._differ.diff(source)
        except KeyError:
            # File not in the tree at self._ref — new/untracked file
            self._warn.print(
                f"[yellow]{rel_path_str}[/yellow] "
                f"[dim]— untracked (no version at {self._ref!r})[/dim]"
            )
            return
        except FileNotFoundError:
            # File was deleted between the event firing and the diff running
            return
        except PluginNotFoundError:
            # No parser registered for this file extension — silently ignore
            return
        except Exception as exc:
            self._warn.print(f"[red]Error diffing {rel_path_str!r}:[/red] {exc}")
            return

        # ── Render ────────────────────────────────────────────────────────
        from rich.rule import Rule

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._out.print(Rule(title=f"[cyan]{rel_path_str}[/cyan]  [dim]{ts}[/dim]"))

        if not diff.changes:
            self._out.print(f"[dim]{rel_path_str} — no changes[/dim]")
            return
        if diff.is_style_only:
            self._out.print(f"[dim]{rel_path_str} — style changes only[/dim]")
            return

        self._render_fn(diff)
