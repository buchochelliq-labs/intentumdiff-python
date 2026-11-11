"""#328: index-cache and streaming help describe the shipped behaviour, which the tests observe."""

from __future__ import annotations

import subprocess
from pathlib import Path

from intentumdiff import CommitDiffer  # noqa: F401  (package import warms the registry)
from intentumdiff.cli import _build_parser
from intentumdiff.core.indexer import Indexer
from intentumdiff.core.models import ChangeStreamPhase
from intentumdiff.differ import SemanticDiffer
from intentumdiff.sources.string_source import StringSource


def _help(*argv: str) -> str:
    parser = _build_parser()
    for name in argv:
        parser = next(
            action.choices[name] for action in parser._actions if getattr(action, "choices", None)
            and name in action.choices
        )
    return parser.format_help()


def test_index_help_promises_only_the_parse_cache() -> None:
    text = " ".join(_help("index").split())
    assert "Only the parse cache is reused" in text
    assert "symbol index is rebuilt on every run" in text
    assert "returns immediately from cache" not in text


def test_index_rebuilds_the_symbol_index_on_a_repeated_call(tmp_path: Path) -> None:
    for argv in (["init", "-q"], ["config", "user.email", "t@e.x"], ["config", "user.name", "t"]):
        subprocess.run(["git", *argv], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "a"], cwd=tmp_path, check=True, capture_output=True)
    indexer = Indexer(SemanticDiffer())
    first = indexer.index_repo(tmp_path, "HEAD")
    second = indexer.index_repo(tmp_path, "HEAD")
    assert (first.from_cache, second.from_cache) == (False, False)
    assert second.files_indexed == first.files_indexed == 1


def test_stream_help_says_events_follow_the_completed_diff() -> None:
    assert "not incremental" in " ".join(_help("file").split())
    assert "after the completed diff" in " ".join(_help("live-server").split())
    assert "not incremental" in " ".join(_help("watch").split())


def test_progressive_stream_emits_only_final_events_after_the_full_diff() -> None:
    differ = SemanticDiffer()
    source = StringSource("x = 1\n", "x = 2\n", filename="a.py")
    events = list(differ.diff_stream_progressive(source))
    assert events and {event.phase for event in events} == {ChangeStreamPhase.FINAL}
    assert [event.change for event in events] == list(differ.diff(source).changes)
