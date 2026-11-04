"""Issue #40: the certified Rust batch path is gated by an allowlist, not a hardcode."""

from __future__ import annotations

from contextlib import closing

import pytest

from intentumdiff import DiffConfig, SemanticDiffer
from intentumdiff.core.models import ChangeType
from intentumdiff.differ import RUST_CERTIFIED_LANGUAGES, _rust_certified_languages


def test_python_is_certified_by_default() -> None:
    assert "python" in RUST_CERTIFIED_LANGUAGES
    assert _rust_certified_languages() == RUST_CERTIFIED_LANGUAGES


def test_force_hook_adds_languages(monkeypatch) -> None:
    monkeypatch.setenv("INTENTUMDIFF_FORCE_RUST_CERTIFIED", "delphi, Elixir")
    forced = _rust_certified_languages()
    assert {"python", "delphi", "elixir"} <= forced
    monkeypatch.delenv("INTENTUMDIFF_FORCE_RUST_CERTIFIED")
    assert _rust_certified_languages() == RUST_CERTIFIED_LANGUAGES


def test_python_still_routes_through_certified_batch() -> None:
    diff = SemanticDiffer().diff_strings(
        "def f(p):\n    return p\n",
        "import os\n\ndef f(p):\n    return os.path.basename(p)\n",
        filename="a.py",
        language_hint="python",
    )
    # The certified batch stamps rust_core metadata; the oracle shape (issue #33) holds.
    assert (diff.metadata or {}).get("engine_telemetry") or (diff.metadata or {}).get("rust_core")
    assert [c.change_type for c in diff.changes] == [ChangeType.ADDITION, ChangeType.MODIFICATION]


@pytest.mark.parametrize("rust_only", ["0", "1"])
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("edit", ["insert", "delete", "unchanged"])
def test_uncertified_parse_errors_use_rust_source_fallback(
    monkeypatch, rust_only, diagnostics, edit
) -> None:
    # This incomplete Delphi program has parse errors. Its exact source evidence
    # must survive through Rust even though Delphi is not batch-certified.
    monkeypatch.setenv("INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE", rust_only)
    monkeypatch.delenv("INTENTUMDIFF_FORCE_RUST_CERTIFIED", raising=False)
    assert "delphi" not in _rust_certified_languages()
    original = "program Demo;\n\nprocedure Alpha;\nbegin\n  WriteLn('Alpha');\nend;\n"
    extended = original.replace("WriteLn('Alpha')", "WriteLn('Alpha changed')")
    old, new = {
        "insert": (original, extended),
        "delete": (extended, original),
        "unchanged": (original, original),
    }[edit]
    with closing(SemanticDiffer(DiffConfig(diagnostics=diagnostics))) as differ:
        diff = differ.diff_strings(old, new, filename="demo.pas", language_hint="delphi")
    assert diff.is_fallback and not diff.is_style_only
    assert diff.parse_errors
    assert diff.metadata["engine_owner"] == "rust"
    assert diff.metadata["semantic_contract"] == "rust_source_fallback_v1"
    if edit == "unchanged":
        assert not diff.changes and not diff.change_groups
        assert not diff.has_semantic_changes
        return
    assert diff.has_semantic_changes
    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == (
        ChangeType.ADDITION if edit == "insert" else ChangeType.DELETION
    )
    added = edit == "insert"
    node = change.new_node if added else change.old_node
    assert node.label == " changed"
    assert (change.old_node if added else change.new_node) is None
    assert (node.position.start_line, node.position.start_col) == (4, 16)
    assert (node.position.end_line, node.position.end_col) == (4, 24)
    assert diff.metadata["source_ranges"] == {
        "old_start_byte": 54, "old_end_byte": 54 if added else 62,
        "new_start_byte": 54, "new_end_byte": 62 if added else 54,
    }
    assert change.confidence < 1 and change.refactoring_kind is None
    assert len(diff.change_groups) == 1
    assert diff.change_groups[0].metadata["semantic_equivalence"] == "unknown"
