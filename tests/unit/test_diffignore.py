"""
tests/unit/test_diffignore.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for :mod:`intentumdiff.core.diffignore` — the ``.diffignore``
file loading and path-matching logic — plus integration tests that verify
:class:`~intentumdiff.core.indexer.Indexer` respects the ignore file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from intentumdiff.core.diffignore import DIFFIGNORE_FILENAME, DiffIgnore, load_diffignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_diffignore(patterns: str) -> DiffIgnore:
    """Build a :class:`DiffIgnore` directly from a pattern string (no file I/O)."""
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitignore", patterns.splitlines())
    return DiffIgnore(spec)


def _make_stub_differ():
    """Minimal SemanticDiffer stub that parses .py files and skips everything else."""
    from intentumdiff.core.models import NodePosition, SemanticNode
    from intentumdiff.plugins.exceptions import PluginNotFoundError

    stub_node = SemanticNode(
        id="n1",
        node_type="module",
        label="",
        children=[],
        position=NodePosition(start_line=1, start_col=0, end_line=1, end_col=0),
        structural_hash="deadbeef",
        metadata={},
    )

    def _parse(content: str, filename: str):
        if not filename.endswith(".py"):
            raise PluginNotFoundError(filename)
        return (stub_node, "python")

    differ = MagicMock()
    differ._cache = None
    differ.parse.side_effect = _parse
    return differ


# ---------------------------------------------------------------------------
# load_diffignore
# ---------------------------------------------------------------------------


class TestLoadDiffignore:
    def test_returns_none_when_file_absent(self, tmp_path: Path) -> None:
        assert load_diffignore(tmp_path) is None

    def test_returns_diffignore_instance_when_present(self, tmp_path: Path) -> None:
        (tmp_path / DIFFIGNORE_FILENAME).write_text("*.lock\n")
        result = load_diffignore(tmp_path)
        assert isinstance(result, DiffIgnore)

    def test_accepts_str_root(self, tmp_path: Path) -> None:
        (tmp_path / DIFFIGNORE_FILENAME).write_text("*.pyc\n")
        assert load_diffignore(str(tmp_path)) is not None

    def test_empty_file_returns_diffignore(self, tmp_path: Path) -> None:
        (tmp_path / DIFFIGNORE_FILENAME).write_text("")
        assert isinstance(load_diffignore(tmp_path), DiffIgnore)

    def test_unreadable_file_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        p = tmp_path / DIFFIGNORE_FILENAME
        p.write_text("*.lock\n")

        # Path.read_text uses io.open internally, so patch at the Path level.
        original_read_text = Path.read_text

        def _raise(self: Path, *args, **kwargs):
            if self == p:
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _raise)
        result = load_diffignore(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# DiffIgnore.is_ignored — pattern semantics
# ---------------------------------------------------------------------------


class TestDiffIgnorePatterns:
    def test_wildcard_extension(self) -> None:
        di = _make_diffignore("*.lock")
        assert di.is_ignored("poetry.lock")
        assert di.is_ignored("subdir/file.lock")
        assert not di.is_ignored("main.py")

    def test_directory_pattern_matches_contents(self) -> None:
        di = _make_diffignore("target/")
        assert di.is_ignored("target/debug/libfoo.a")
        assert di.is_ignored("target/release/binary")
        assert not di.is_ignored("not_target/file.py")

    def test_root_anchored_pattern(self) -> None:
        di = _make_diffignore("/dist")
        assert di.is_ignored("dist/bundle.js")
        # Root-anchored — should NOT match nested occurrences
        assert not di.is_ignored("packages/app/dist/bundle.js")

    def test_double_star_pattern(self) -> None:
        di = _make_diffignore("**/generated/**")
        assert di.is_ignored("src/generated/foo.py")
        assert di.is_ignored("generated/bar.ts")
        assert not di.is_ignored("src/manual/baz.py")

    def test_comment_lines_are_ignored(self) -> None:
        di = _make_diffignore("# this is a comment\n*.log\n")
        assert di.is_ignored("app.log")
        assert not di.is_ignored("# this is a comment")

    def test_blank_lines_are_ignored(self) -> None:
        di = _make_diffignore("\n\n*.log\n\n")
        assert di.is_ignored("app.log")

    def test_negation_un_ignores_specific_file(self) -> None:
        di = _make_diffignore("*.log\n!important.log\n")
        assert di.is_ignored("debug.log")
        assert not di.is_ignored("important.log")

    def test_unmatched_path_returns_false(self) -> None:
        di = _make_diffignore("*.lock")
        assert not di.is_ignored("main.py")

    def test_multiple_patterns(self) -> None:
        di = _make_diffignore("*.lock\ndist/\n*.min.js\n")
        assert di.is_ignored("poetry.lock")
        assert di.is_ignored("dist/bundle.js")
        assert di.is_ignored("app.min.js")
        assert not di.is_ignored("src/app.js")


# ---------------------------------------------------------------------------
# Integration: Indexer.index_directory respects .diffignore
# ---------------------------------------------------------------------------


class TestIndexerDiffignoreIntegration:
    def test_ignored_file_is_not_indexed(self, tmp_path: Path) -> None:
        from intentumdiff.core.indexer import Indexer

        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "generated.py").write_text("y = 2")
        (tmp_path / DIFFIGNORE_FILENAME).write_text("generated.py\n")

        indexer = Indexer(_make_stub_differ())
        result = indexer.index_directory(tmp_path)

        assert result.files_ignored == 1
        assert "generated.py" in result.ignored_files
        assert result.files_indexed == 1  # only main.py

    def test_directory_pattern_excludes_subtree(self, tmp_path: Path) -> None:
        from intentumdiff.core.indexer import Indexer

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()
        (gen_dir / "foo.py").write_text("a = 1")
        (gen_dir / "bar.py").write_text("b = 2")
        (tmp_path / DIFFIGNORE_FILENAME).write_text("generated/\n")

        indexer = Indexer(_make_stub_differ())
        result = indexer.index_directory(tmp_path)

        assert result.files_ignored == 2
        assert result.files_indexed == 1  # only src/main.py
        assert all("generated/" in p for p in result.ignored_files)

    def test_no_diffignore_file_leaves_result_unchanged(self, tmp_path: Path) -> None:
        from intentumdiff.core.indexer import Indexer

        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")
        # No .diffignore file present

        indexer = Indexer(_make_stub_differ())
        result = indexer.index_directory(tmp_path)

        assert result.files_ignored == 0
        assert result.ignored_files == []
        assert result.files_indexed == 2

    def test_ignored_count_in_result_fields(self, tmp_path: Path) -> None:
        from intentumdiff.core.indexer import Indexer

        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text("pass")
        (tmp_path / DIFFIGNORE_FILENAME).write_text("b.py\nc.py\n")

        indexer = Indexer(_make_stub_differ())
        result = indexer.index_directory(tmp_path)

        assert result.files_ignored == 2
        assert set(result.ignored_files) == {"b.py", "c.py"}
        assert result.files_indexed == 1
