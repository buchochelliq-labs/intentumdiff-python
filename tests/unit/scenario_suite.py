"""Abstract standard-scenario suite — the SAME canonical scenarios, one implementation per language.

This is the reusable base for the per-language scenario tests. It is NOT collected itself
(``__test__ = False`` / no ``Test`` prefix). Each language provides a subclass in its own file
(``test_scenarios_<lang>.py``) that sets ``language`` / ``filename`` and supplies a ``CASES`` dict
mapping scenario name -> :class:`Case`.

Two family bases define the standard test methods (so every language of that family gets the same
set): :class:`LineScenarioSuite` (text/markdown/config, line-oriented) and
:class:`CodeScenarioSuite` (programming languages, entity-oriented). Markdown adds a few
section-aware scenarios via :class:`MarkdownScenarioSuite`.

Each ``Case`` asserts the *expected clean behavior* from the intentdiff-diff-expectations oracle.
A scenario the engine does not yet satisfy sets ``xfail=<reason>``: the assertion still RUNS and
fails, so the gap is a failing test on the record; when the engine is fixed it reports XPASS
("remove the xfail"). A scenario a language legitimately omits is simply absent from ``CASES`` and
the inherited test skips.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from intentdiff import SemanticDiffer
from intentdiff.core.models import ChangeType, RefactoringKind, SemanticDiff

_RENAME_KINDS = {
    RefactoringKind.RENAME_SYMBOL,
    RefactoringKind.RENAME_CLASS,
    RefactoringKind.RENAME_METHOD,
    RefactoringKind.RENAME_VARIABLE,
}


@dataclass(frozen=True)
class Case:
    old: str
    new: str
    additions: tuple[str, ...] = ()
    deletions: tuple[str, ...] = ()
    modifications: tuple[tuple[str, str], ...] = ()
    renames: tuple[tuple[str, str], ...] = ()
    moves: tuple[str, ...] = ()              # labels that MUST appear as MOVE
    exact_total: int | None = None
    forbid_node_types: tuple[str, ...] = ()
    style_only: bool = False
    not_style_only: bool = False
    xfail: str | None = None


def run_case(language: str, filename: str, case: Case) -> SemanticDiff:
    return SemanticDiffer().diff_strings(case.old, case.new, filename=filename, language_hint=language)


def _labels(diff: SemanticDiff, change_type: ChangeType) -> list[str]:
    out = []
    for change in diff.changes:
        if change.change_type != change_type:
            continue
        node = change.old_node if change_type == ChangeType.DELETION else change.new_node
        node = node or change.new_node or change.old_node
        if node is not None:
            out.append(node.label)
    return out


def check_case(language: str, scenario: str, case: Case, diff: SemanticDiff) -> None:
    tag = f"{language}/{scenario}"
    assert not diff.is_fallback, f"{tag}: unexpected parser fallback"
    assert not diff.parse_errors, f"{tag}: parse errors {diff.parse_errors}"

    if case.style_only:
        assert diff.is_style_only and diff.changes == [], f"{tag}: expected style-only, got {len(diff.changes)}"
        return
    if case.not_style_only:
        assert not diff.is_style_only, f"{tag}: expected a real change, got style-only"

    add_labels = _labels(diff, ChangeType.ADDITION)
    del_labels = _labels(diff, ChangeType.DELETION)
    for want in case.additions:
        assert want in add_labels, f"{tag}: missing ADDITION {want!r} (got {add_labels})"
    for want in case.deletions:
        assert want in del_labels, f"{tag}: missing DELETION {want!r} (got {del_labels})"
    for old_label, new_label in case.modifications:
        assert any(
            c.change_type == ChangeType.MODIFICATION
            and c.old_node is not None and c.new_node is not None
            and c.old_node.label == old_label and c.new_node.label == new_label
            for c in diff.changes
        ), f"{tag}: missing MODIFICATION {old_label!r}->{new_label!r}"
    for old_label, new_label in case.renames:
        assert any(
            c.change_type == ChangeType.REFACTORING and c.refactoring_kind in _RENAME_KINDS
            and c.old_node is not None and c.new_node is not None
            and old_label in c.old_node.label and new_label in c.new_node.label
            for c in diff.changes
        ), f"{tag}: missing REFACTORING rename {old_label!r}->{new_label!r} (got {[(x.change_type.value, (x.new_node or x.old_node).node_type) for x in diff.changes]})"
    for want in case.moves:
        assert any(
            c.change_type == ChangeType.MOVE
            and (c.new_node or c.old_node) is not None
            and want in (c.new_node or c.old_node).label
            for c in diff.changes
        ), f"{tag}: missing MOVE {want!r} (got {[(x.change_type.value, (x.new_node or x.old_node).label) for x in diff.changes]})"

    if case.exact_total is not None:
        assert len(diff.changes) == case.exact_total, (
            f"{tag}: expected {case.exact_total} changes, got {len(diff.changes)}: "
            f"{[(c.change_type.value, (c.new_node or c.old_node).node_type) for c in diff.changes]}"
        )
    for forbidden in case.forbid_node_types:
        assert not any(
            (c.new_node or c.old_node) is not None and (c.new_node or c.old_node).node_type == forbidden
            for c in diff.changes
        ), f"{tag}: forbidden node_type {forbidden!r} leaked"


class _ScenarioSuite:
    # NB: do NOT set ``__test__ = False`` here — it inherits into the concrete Test* subclasses
    # and stops them being collected. Pytest already skips the ``*Suite`` bases by name.
    language: str = ""
    filename: str = ""
    CASES: dict[str, Case] = {}

    def _run(self, scenario: str) -> None:
        case = self.CASES.get(scenario)
        if case is None:
            pytest.skip(f"{self.language}: no case for scenario {scenario!r}")
        try:
            check_case(self.language, scenario, case, run_case(self.language, self.filename, case))
        except AssertionError:
            if case.xfail:
                pytest.xfail(case.xfail)
            raise
        else:
            if case.xfail:
                pytest.fail(f"XPASS {self.language}/{scenario}: now passes — remove xfail ({case.xfail})")


class LineScenarioSuite(_ScenarioSuite):
    """Line-oriented file types (text/markdown/config): the standard positional line scenarios."""

    def test_add_at_end(self): self._run("add_at_end")
    def test_add_in_middle(self): self._run("add_in_middle")
    def test_add_at_start(self): self._run("add_at_start")
    def test_delete_at_end(self): self._run("delete_at_end")
    def test_delete_in_middle(self): self._run("delete_in_middle")
    def test_delete_at_start(self): self._run("delete_at_start")
    def test_modify_line(self): self._run("modify_line")
    def test_modify_two_lines(self): self._run("modify_two_lines")
    def test_reorder_lines(self): self._run("reorder_lines")
    def test_whitespace_only(self): self._run("whitespace_only")
    def test_identical(self): self._run("identical")


class MarkdownScenarioSuite(LineScenarioSuite):
    """Markdown adds section-aware scenarios on top of the line-oriented set."""

    def test_add_section_at_end(self): self._run("add_section_at_end")
    def test_add_bullet(self): self._run("add_bullet")
    def test_rename_heading(self): self._run("rename_heading")
    def test_move_section(self): self._run("move_section")


class CodeScenarioSuite(_ScenarioSuite):
    """Programming languages: the standard entity-oriented scenarios."""

    def test_add_fn_at_end(self): self._run("add_fn_at_end")
    def test_add_fn_in_middle(self): self._run("add_fn_in_middle")
    def test_delete_fn_at_end(self): self._run("delete_fn_at_end")
    def test_delete_fn_in_middle(self): self._run("delete_fn_in_middle")
    def test_modify_fn_body(self): self._run("modify_fn_body")
    def test_rename_fn(self): self._run("rename_fn")
    def test_rename_param(self): self._run("rename_param")
    def test_move_fn(self): self._run("move_fn")
    def test_add_param(self): self._run("add_param")
    def test_style_only(self): self._run("style_only")
    def test_async_toggle(self): self._run("async_toggle")
    def test_delete_and_add_unrelated(self): self._run("delete_and_add_unrelated")
    def test_add_decorator(self): self._run("add_decorator")
    def test_add_import_and_use(self): self._run("add_import_and_use")
    # Issue #42 hardening: a trivial/empty body replaced by a real body is MEANINGFUL,
    # never style-only (the #41 class).
    def test_trivial_body_to_real_body(self): self._run("trivial_body_to_real_body")
