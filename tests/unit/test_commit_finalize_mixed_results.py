"""#332: finalize_commit_diff accepts everything iter_file_diffs yields.

A per-file FileDiffError must neither crash finalisation nor hide the rest of the commit:
the real cross-file move between the other files is still reported, and the skipped file
is visible in the returned CommitDiff.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from intentumdiff.core.commit_differ import CommitDiffer, FileDiffError, FileDiffResult

_HELPER = "def helper(value):\n    total = value * 2\n    return total + 1\n"


def _git(cwd: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


def test_finalize_mixes_a_file_error_with_a_real_cross_file_move(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text(_HELPER + "\n\ndef keep():\n    return 0\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def other():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "one")
    (tmp_path / "a.py").write_text("def keep():\n    return 0\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def other():\n    return 1\n\n\n" + _HELPER, encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "move helper")

    differ = CommitDiffer()
    streamed = list(differ.iter_file_diffs(tmp_path, "HEAD~1", "HEAD"))
    assert [type(item) for item in streamed] == [FileDiffResult, FileDiffResult]
    failed = FileDiffError("huge.py", "huge.py", "size limit exceeded", "pipeline_error")

    commit = differ.finalize_commit_diff(
        [*streamed, failed], old_ref="HEAD~1", new_ref="HEAD", repo_path=tmp_path
    )

    assert sorted(diff.new_filename for diff in commit.file_diffs) == ["a.py", "b.py"]
    moves = [(c.symbol_name, c.old_file, c.new_file) for c in commit.cross_file_changes]
    assert ("module.helper", "a.py", "b.py") in moves
    assert commit.parse_errors == ["huge.py: size limit exceeded"]
