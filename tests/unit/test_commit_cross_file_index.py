"""Commit cross-file index completeness is core's (core #298, Python binding).

One changed file that cannot be indexed (here: the parser runs out of fuel on a big file)
used to make ``CommitDiffer`` raise "Commit semantic index incomplete" and discard every
cross-file change. Core's ``commit_cross_file_index`` now decides: the file is reported in
``index_errors``, the status is ``partial``, and moves between the other files survive.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from intentumdiff import FUEL_UNLIMITED, ChangeType, CommitDiffer, DiffConfig

# Small enough that the two small files parse, far too small for the big one. The per-file
# diffs size-scale their fuel, so only the commit index's parse runs out.
_INDEX_FUEL = 5_000_000

A_OLD = "def moved(x):\n    return x + 1\n\n\ndef keep_a():\n    return 'a'\n"
A_NEW = "def keep_a():\n    return 'a'\n"
B_OLD = "def keep_b():\n    return 'b'\n"
B_NEW = "def keep_b():\n    return 'b'\n\n\ndef moved(x):\n    return x + 1\n"
BIG_OLD = "".join(f"def f{i}(a, b):\n    return a + b * {i}\n\n" for i in range(600))
BIG_NEW = BIG_OLD + "def tail():\n    return 0\n"


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    _run(tmp_path, "init", "-q")
    _run(tmp_path, "config", "user.email", "test@example.invalid")
    _run(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(A_OLD, encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(B_OLD, encoding="utf-8")
    (tmp_path / "pkg" / "big.py").write_text(BIG_OLD, encoding="utf-8")
    _run(tmp_path, "add", ".")
    _run(tmp_path, "commit", "-q", "-m", "before")
    (tmp_path / "pkg" / "a.py").write_text(A_NEW, encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(B_NEW, encoding="utf-8")
    (tmp_path / "pkg" / "big.py").write_text(BIG_NEW, encoding="utf-8")
    _run(tmp_path, "add", ".")
    _run(tmp_path, "commit", "-q", "-m", "after")
    return tmp_path


def test_unindexable_big_file_keeps_real_cross_file_move(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit = CommitDiffer(DiffConfig(plugin_fuel=_INDEX_FUEL)).diff_commit(
        str(repo), "HEAD~1", "HEAD"
    )

    assert sorted(diff.new_filename for diff in commit.file_diffs) == [
        "pkg/a.py", "pkg/b.py", "pkg/big.py",
    ]
    moves = [c for c in commit.cross_file_changes if c.change_type == ChangeType.MOVE_TO_MODULE]
    assert [(m.symbol_name, m.old_file, m.new_file) for m in moves] == [
        ("module.moved", "pkg/a.py", "pkg/b.py")
    ]
    assert commit.cross_file_index_status == "partial"
    assert commit.cross_file_index_complete is False
    assert commit.indexed_file_count == 2
    assert [(e.path, e.code) for e in commit.index_errors] == [("pkg/big.py", "fuel_exceeded")]
    assert "FUEL_EXCEEDED" in commit.index_errors[0].reason


def test_fully_indexed_commit_is_complete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit = CommitDiffer(DiffConfig(plugin_fuel=FUEL_UNLIMITED)).diff_commit(
        str(repo), "HEAD~1", "HEAD"
    )

    assert commit.cross_file_index_status == "complete"
    assert commit.cross_file_index_complete is True
    assert commit.indexed_file_count == 3
    assert commit.index_errors == []
    assert any(
        c.change_type == ChangeType.MOVE_TO_MODULE and c.symbol_name.endswith("moved")
        for c in commit.cross_file_changes
    )
