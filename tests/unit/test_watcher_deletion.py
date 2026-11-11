"""#326: the watcher's deletion contract, exercised with a real (bounded) watchdog observer.

Contract: a deleted watched file is reported as deleted, not reviewed (`intentumdiff git`
reviews deletions); a delete followed by a re-create within the debounce window (an atomic
save) is reviewed once as an ordinary save; no debounce timer outlives either.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

from intentumdiff.core.models import SemanticDiff
from intentumdiff.watcher import FileWatcher


def _repo(tmp_path: Path) -> Path:
    for argv in (["init", "-q"], ["config", "user.email", "t@e.x"], ["config", "user.name", "t"]):
        subprocess.run(["git", *argv], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "x.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _watcher(root: Path, debounce: float) -> tuple[FileWatcher, list[str], MagicMock]:
    differ = MagicMock()
    result = SemanticDiff(old_filename="x.py", new_filename="x.py", language="python")
    # Read the source like the real differ does, so core's working-tree read decides absence.
    differ.diff.side_effect = lambda source: (source.get_content(), result)[1]
    render_fn = MagicMock()
    watcher = FileWatcher([root], differ, render_fn=render_fn, debounce=debounce)
    lines: list[str] = []
    watcher._out = MagicMock()
    watcher._out.print = lambda msg, **kw: lines.append(str(msg))
    return watcher, lines, render_fn


def _until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_deleting_a_watched_file_reports_deleted_and_leaves_no_timer(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    watcher, lines, render_fn = _watcher(root, debounce=0.05)
    watcher.start()
    try:
        (root / "x.py").unlink()
        assert _until(lambda: any("deleted" in line for line in lines)), lines
        assert _until(lambda: not watcher._timers)
    finally:
        watcher.stop()
    assert [line for line in lines if "deleted" in line] == [
        f"{(root / 'x.py').resolve()} — deleted (deletions are not reviewed while watching)"
    ]
    render_fn.assert_not_called()


def test_delete_then_recreate_within_the_debounce_is_one_review(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    watcher, lines, _render_fn = _watcher(root, debounce=0.5)
    watcher.start()
    try:
        (root / "x.py").unlink()
        (root / "x.py").write_text("x = 2\n", encoding="utf-8")
        assert _until(lambda: watcher._differ.diff.call_count >= 1)
        time.sleep(0.6)  # any second timer would have fired by now
        assert _until(lambda: not watcher._timers)
    finally:
        watcher.stop()
    assert watcher._differ.diff.call_count == 1  # reviewed once, as an ordinary save
    assert not any("deleted" in line for line in lines)
