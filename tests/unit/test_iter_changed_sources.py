"""
tests/unit/test_iter_changed_sources.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for iter_changed_sources() and SemanticDiffer.diff_commit().

Each test creates a temporary git repository with the minimum history
needed to exercise the scenario under test.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from intentdiff.core.models import ChangeType
from intentdiff.differ import SemanticDiffer
from intentdiff.sources.git_source import (
    collect_working_tree_python_sources_fast,
    iter_changed_sources,
)


# ---------------------------------------------------------------------------
# Helpers (shared with test_git_source.py, kept local for clarity)
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test")
        cfg.set_value("user", "email", "test@example.com")
    return repo


def _commit_file(repo: git.Repo, rel_path: str, content: str, message: str) -> git.Commit:
    full = Path(repo.working_tree_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    repo.index.add([rel_path])
    return repo.index.commit(message)


def _delete_file(repo: git.Repo, rel_path: str, message: str) -> git.Commit:
    repo.index.remove([rel_path], working_tree=True)
    return repo.index.commit(message)


def _make_repo_with_bare_origin(tmp_path: Path) -> git.Repo:
    worktree = tmp_path / "work"
    worktree.mkdir()
    repo = _make_repo(worktree)
    _commit_file(
        repo,
        "src/order.ts",
        "\n".join(
            [
                "export function parseOrder(input: string): number {",
                "    return Number.parseInt(input, 10);",
                "}",
                "",
                "export function formatOrder(value: number): string {",
                "    return value.toFixed(2);",
                "}",
                "",
            ]
        ),
        "initial typescript",
    )
    _commit_file(
        repo,
        "scripts/review.ps1",
        "\n".join(
            [
                "function Invoke-Parse {",
                "    param([string]$Value)",
                "    return $Value.Trim()",
                "}",
                "",
                "function Invoke-Format {",
                "    param([string]$Value)",
                "    return $Value.ToUpperInvariant()",
                "}",
                "",
            ]
        ),
        "initial powershell",
    )
    repo.git.branch("-M", "main")
    bare_path = tmp_path / "origin.git"
    git.Repo.init(bare_path, bare=True)
    repo.create_remote("origin", str(bare_path))
    repo.git.push("-u", "origin", "main")
    return repo


def _write(repo: git.Repo, rel_path: str, content: str) -> None:
    full = Path(repo.working_tree_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _semantic_labels(diff, change_type: ChangeType) -> set[str]:
    labels: set[str] = set()

    def collect(node) -> None:
        if node is None:
            return
        if node.label:
            labels.add(node.label)
        for child in getattr(node, "children", ()) or ():
            collect(child)

    for change in diff.changes:
        if change.change_type != change_type:
            continue
        collect(change.old_node)
        collect(change.new_node)
    return labels


# ---------------------------------------------------------------------------
# iter_changed_sources
# ---------------------------------------------------------------------------


class TestIterChangedSources:
    def test_modified_file_yields_old_and_new_content(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "app.py", "x = 1\n", "v1")
        _commit_file(repo, "app.py", "x = 2\n", "v2")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))
        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert "x = 1" in old
        assert "x = 2" in new
        assert "app.py" in new_path
        assert staging_status is None

    def test_added_file_has_empty_old_content(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "base.py", "pass\n", "init")
        _commit_file(repo, "new_module.py", "y = 42\n", "add")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))
        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert old == ""
        assert "y = 42" in new
        assert "new_module.py" in new_path
        assert staging_status is None

    def test_deleted_file_has_empty_new_content(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "gone.py", "z = 0\n", "init")
        _delete_file(repo, "gone.py", "remove")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))
        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert "z = 0" in old
        assert new == ""
        assert old_path == "gone.py"
        assert new_path == "gone.py"
        assert staging_status is None

    def test_repo_name_with_dot_and_multi_extension_path_stays_relative(self, tmp_path):
        repo_root = tmp_path / "team.repo"
        repo_root.mkdir()
        repo = _make_repo(repo_root)
        rel_path = "pkg/service.config.test.py"
        _commit_file(repo, rel_path, "value = 1\n", "init")
        _commit_file(repo, rel_path, "value = 2\n", "update")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))

        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert "value = 1" in old
        assert "value = 2" in new
        assert old_path == rel_path
        assert new_path == rel_path
        assert "team.repo" not in old_path
        assert staging_status is None

    def test_multiple_files_all_yielded(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "a.py", "a = 1\n", "init")
        # Modify a.py and add b.py in one commit
        (Path(tmp_path) / "a.py").write_text("a = 2\n", encoding="utf-8")
        (Path(tmp_path) / "b.py").write_text("b = 3\n", encoding="utf-8")
        repo.index.add(["a.py", "b.py"])
        repo.index.commit("update")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))
        paths = {r[3] for r in results}
        assert "a.py" in paths
        assert "b.py" in paths

    def test_no_changes_yields_nothing(self, tmp_path):
        repo = _make_repo(tmp_path)
        c1 = _commit_file(repo, "x.py", "pass\n", "init")
        # Commit the same content again (no diff)
        _commit_file(repo, "x.py", "pass\n", "no-op")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))
        assert results == []

    def test_sha_refs_work(self, tmp_path):
        repo = _make_repo(tmp_path)
        c1 = _commit_file(repo, "v.py", "v = 1\n", "first")
        c2 = _commit_file(repo, "v.py", "v = 2\n", "second")

        results = list(iter_changed_sources(repo.working_dir, c1.hexsha, c2.hexsha))
        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert "v = 1" in old
        assert "v = 2" in new
        assert staging_status is None

    def test_renamed_file_carries_both_paths(self, tmp_path):
        """Renames yield old_path != new_path, preserving both."""
        repo = _make_repo(tmp_path)
        # Enable rename detection in git config
        with repo.config_writer() as cfg:
            cfg.set_value("diff", "renames", "true")
        _commit_file(repo, "old_name.py", "x = 1\n", "add")

        old_full = Path(tmp_path) / "old_name.py"
        new_full = Path(tmp_path) / "new_name.py"
        old_full.rename(new_full)
        repo.index.remove(["old_name.py"])
        repo.index.add(["new_name.py"])
        repo.index.commit("rename")

        results = list(iter_changed_sources(repo.working_dir, "HEAD~1", "HEAD"))
        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert old_path != new_path
        assert "old_name.py" in old_path
        assert "new_name.py" in new_path
        assert staging_status is None

    def test_working_tree_untracked_file_yields_empty_old_content(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "base.py", "base = True\n", "init")
        untracked = Path(tmp_path) / "new_module.py"
        untracked.write_text("def new_function():\n    return 1\n", encoding="utf-8")

        results = list(iter_changed_sources(repo.working_dir, "HEAD", ""))

        assert len(results) == 1
        old, new, old_path, new_path, staging_status = results[0]
        assert old == ""
        assert "def new_function" in new
        assert old_path == "new_module.py"
        assert new_path == "new_module.py"
        assert staging_status == "untracked"

    def test_fast_working_tree_python_collector_reads_python_changes(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "app.py", "value = 1\n", "init")
        (Path(tmp_path) / "app.py").write_text("value = 2\n", encoding="utf-8")

        result = collect_working_tree_python_sources_fast(repo.working_dir, "HEAD")

        assert result is not None
        sources, fallback_reason = result
        assert fallback_reason is None
        assert len(sources) == 1
        old, new, old_path, new_path, staging_status = sources[0]
        assert old.replace("\r\n", "\n") == "value = 1\n"
        assert new.replace("\r\n", "\n") == "value = 2\n"
        assert old_path == "app.py"
        assert new_path == "app.py"
        assert staging_status == "unstaged"

    def test_fast_working_tree_python_collector_reads_untracked_python_changes(
        self,
        tmp_path,
    ):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "app.py", "value = 1\n", "init")
        (Path(tmp_path) / "new_module.py").write_text(
            "def new_function():\n    return 1\n",
            encoding="utf-8",
        )

        result = collect_working_tree_python_sources_fast(repo.working_dir, "HEAD")

        assert result is not None
        sources, fallback_reason = result
        assert fallback_reason is None
        assert len(sources) == 1
        old, new, old_path, new_path, staging_status = sources[0]
        assert old == ""
        assert "def new_function" in new
        assert old_path == "new_module.py"
        assert new_path == "new_module.py"
        assert staging_status == "untracked"

    def test_competitor_live_buffer_untracked_file_has_visible_review_evidence(
        self,
        tmp_path,
    ):
        from intentdiff.core.models import ChangeType
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "base.py", "base = True\n", "init")
        (Path(tmp_path) / "scratch.py").write_text(
            "def scratch():\n    return 42\n",
            encoding="utf-8",
        )

        differ = SemanticDiffer()
        diffs = differ.diff_commit(repo_path=tmp_path)

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.new_filename == "scratch.py"
        assert diff.staging_status == "untracked"
        assert diff.metadata.get("file_lifecycle") == "added"
        assert not diff.is_style_only
        assert any(change.change_type == ChangeType.ADDITION for change in diff.changes)

    def test_competitor_unsupported_file_fallback_is_visible_generic_evidence(self):
        from intentdiff.differ import SemanticDiffer

        differ = SemanticDiffer()
        diff = differ.diff_strings(
            "one,two\n1,2\n",
            "one,two\n1,3\n",
            filename="metrics.unknownext",
            language_hint="not-a-real-language",
        )

        assert diff.language == "generic"
        assert not diff.is_fallback
        assert not diff.parse_errors
        assert diff.changes or diff.change_groups

    def test_fast_working_tree_python_collector_rejects_mixed_changes(self, tmp_path):
        repo = _make_repo(tmp_path)
        _commit_file(repo, "app.py", "value = 1\n", "init app")
        _commit_file(repo, "README.md", "old\n", "init readme")
        (Path(tmp_path) / "app.py").write_text("value = 2\n", encoding="utf-8")
        (Path(tmp_path) / "README.md").write_text("new\n", encoding="utf-8")

        result = collect_working_tree_python_sources_fast(repo.working_dir, "HEAD")

        assert result is not None
        sources, fallback_reason = result
        assert sources == []
        assert fallback_reason == (
            "certified commit JSON requires all changed files to be Python"
        )


# ---------------------------------------------------------------------------
# SemanticDiffer.diff_commit
# ---------------------------------------------------------------------------


class TestDiffCommit:
    def test_diff_commit_returns_list_of_diffs(self, tmp_path):
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "calc.py", "def add(a, b):\n    return a + b\n", "v1")
        _commit_file(repo, "calc.py", "def add(a, b):\n    return a - b\n", "v2")

        differ = SemanticDiffer()
        diffs = differ.diff_commit(repo_path=tmp_path, old_ref="HEAD~1", new_ref="HEAD")

        assert isinstance(diffs, list)
        # calc.py should be parsed — python parser is available
        assert len(diffs) >= 1
        assert all(hasattr(d, "has_semantic_changes") for d in diffs)

    def test_diff_commit_style_only_change(self, tmp_path):
        """Adding a comment only → style-only diff."""
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "mod.py", "x = 1\n", "v1")
        _commit_file(repo, "mod.py", "# comment\nx = 1\n", "v2")

        differ = SemanticDiffer()
        diffs = differ.diff_commit(repo_path=tmp_path, old_ref="HEAD~1", new_ref="HEAD")

        assert len(diffs) == 1
        assert diffs[0].is_style_only

    def test_diff_commit_skips_unparseable_files(self, tmp_path):
        """Binary or unknown-language files must be silently skipped."""
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "data.csv", "a,b\n1,2\n", "v1")
        _commit_file(repo, "data.csv", "a,b\n1,3\n", "v2")

        differ = SemanticDiffer()
        # Should not raise even though .csv has no parser
        diffs = differ.diff_commit(repo_path=tmp_path, old_ref="HEAD~1", new_ref="HEAD")
        assert isinstance(diffs, list)

    def test_diff_commit_uses_default_refs(self, tmp_path):
        """diff_commit defaults old_ref to HEAD, new_ref to working tree.

        Make a change on disk (not committed) so the working-tree diff finds it.
        """
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "fn.py", "def f(): pass\n", "v1")
        # Modify on disk without committing — working tree diff should pick this up.
        (Path(tmp_path) / "fn.py").write_text("def f(): return 1\n", encoding="utf-8")

        differ = SemanticDiffer()
        diffs = differ.diff_commit(repo_path=tmp_path)  # defaults: old=HEAD, new=working tree

        assert len(diffs) == 1
        assert diffs[0].new_filename == "fn.py"
        assert diffs[0].staging_status in ("staged", "unstaged")

    def test_diff_commit_includes_untracked_file(self, tmp_path):
        """Untracked working-tree files are treated as additions."""
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "base.py", "base = True\n", "init")
        (Path(tmp_path) / "new_module.py").write_text(
            "def new_function():\n    return 1\n",
            encoding="utf-8",
        )

        differ = SemanticDiffer()
        diffs = differ.diff_commit(repo_path=tmp_path)

        assert len(diffs) == 1
        assert diffs[0].new_filename == "new_module.py"
        assert diffs[0].has_semantic_changes
        assert diffs[0].staging_status == "untracked"

    def test_diff_commit_accepts_subdirectory(self, tmp_path):
        """repo_path can be any directory inside the repo."""
        from intentdiff.differ import SemanticDiffer

        repo = _make_repo(tmp_path)
        _commit_file(repo, "pkg/util.py", "A = 1\n", "v1")
        _commit_file(repo, "pkg/util.py", "A = 2\n", "v2")

        sub = tmp_path / "pkg"
        differ = SemanticDiffer()
        diffs = differ.diff_commit(repo_path=sub, old_ref="HEAD~1", new_ref="HEAD")

        assert len(diffs) == 1

    def test_unpushed_commit_uses_tracking_branch_and_matches_cli_flow(
        self,
        tmp_path,
        monkeypatch,
    ):
        repo = _make_repo_with_bare_origin(tmp_path)
        _write(
            repo,
            "src/order.ts",
            "\n".join(
                [
                    "export function parseOrder(input: string): number {",
                    "    return Number.parseInt(input, 10);",
                    "}",
                    "",
                    "export function validateOrder(value: number): boolean {",
                    "    return value > 0;",
                    "}",
                    "",
                    "export function formatOrder(value: number): string {",
                    "    return value.toFixed(2);",
                    "}",
                    "",
                ]
            ),
        )
        repo.index.add(["src/order.ts"])
        repo.index.commit("add validation without push")

        source_rows = list(iter_changed_sources(repo.working_dir, "HEAD", ":unpushed"))
        assert [(row[3], row[4]) for row in source_rows] == [
            ("src/order.ts", "unpushed")
        ]

        differ = SemanticDiffer()
        api_diffs = differ.diff_commit(
            repo_path=repo.working_tree_dir,
            old_ref="HEAD",
            new_ref=":unpushed",
        )

        assert len(api_diffs) == 1
        api_diff = api_diffs[0]
        assert api_diff.new_filename == "src/order.ts"
        assert api_diff.staging_status == "unpushed"
        assert api_diff.metadata.get("file_lifecycle") == "modified"
        assert "validateOrder" in _semantic_labels(api_diff, ChangeType.ADDITION)
        assert "validateOrder" not in _semantic_labels(api_diff, ChangeType.MOVE)

        from intentdiff.cli import _build_parser, _cmd_git

        captured = []
        monkeypatch.setattr(
            "intentdiff.cli._shared._render",
            lambda diff, fmt, output, fuel=None: captured.append(diff),
        )
        parser = _build_parser()
        monkeypatch.chdir(repo.working_tree_dir)
        args = parser.parse_args(["git", "--unpushed"])

        _cmd_git(args)

        assert [(diff.new_filename, diff.staging_status) for diff in captured] == [
            ("src/order.ts", "unpushed")
        ]
        assert _semantic_labels(captured[0], ChangeType.ADDITION) == _semantic_labels(
            api_diff,
            ChangeType.ADDITION,
        )

    def test_working_tree_change_after_push_is_unstaged_addition_not_move(
        self,
        tmp_path,
    ):
        repo = _make_repo_with_bare_origin(tmp_path)
        _write(
            repo,
            "src/order.ts",
            "\n".join(
                [
                    "export function parseOrder(input: string): number {",
                    "    return Number.parseInt(input, 10);",
                    "}",
                    "",
                    "export function normalizeOrder(value: number): number {",
                    "    return Math.max(value, 0);",
                    "}",
                    "",
                    "export function formatOrder(value: number): string {",
                    "    return value.toFixed(2);",
                    "}",
                    "",
                ]
            ),
        )

        diffs = SemanticDiffer().diff_commit(repo_path=repo.working_tree_dir)

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.new_filename == "src/order.ts"
        assert diff.staging_status == "unstaged"
        assert "normalizeOrder" in _semantic_labels(diff, ChangeType.ADDITION)
        assert "normalizeOrder" not in _semantic_labels(diff, ChangeType.MOVE)

    def test_staged_change_after_push_is_staged_addition_not_move(self, tmp_path):
        repo = _make_repo_with_bare_origin(tmp_path)
        _write(
            repo,
            "src/order.ts",
            "\n".join(
                [
                    "export function parseOrder(input: string): number {",
                    "    return Number.parseInt(input, 10);",
                    "}",
                    "",
                    "export function summarizeOrder(value: number): string {",
                    "    return `order-${value}`;",
                    "}",
                    "",
                    "export function formatOrder(value: number): string {",
                    "    return value.toFixed(2);",
                    "}",
                    "",
                ]
            ),
        )
        repo.index.add(["src/order.ts"])

        diffs = SemanticDiffer().diff_commit(repo_path=repo.working_tree_dir)

        assert len(diffs) == 1
        diff = diffs[0]
        assert diff.new_filename == "src/order.ts"
        assert diff.staging_status == "staged"
        assert "summarizeOrder" in _semantic_labels(diff, ChangeType.ADDITION)
        assert "summarizeOrder" not in _semantic_labels(diff, ChangeType.MOVE)

    def test_multi_file_commit_preserves_insert_and_move_intent(self, tmp_path):
        repo = _make_repo_with_bare_origin(tmp_path)
        _write(
            repo,
            "src/order.ts",
            "\n".join(
                [
                    "export function parseOrder(input: string): number {",
                    "    return Number.parseInt(input, 10);",
                    "}",
                    "",
                    "export function enrichOrder(value: number): number {",
                    "    return value + 1;",
                    "}",
                    "",
                    "export function formatOrder(value: number): string {",
                    "    return value.toFixed(2);",
                    "}",
                    "",
                ]
            ),
        )
        _write(
            repo,
            "scripts/review.ps1",
            "\n".join(
                [
                    "function Invoke-Format {",
                    "    param([string]$Value)",
                    "    return $Value.ToUpperInvariant()",
                    "}",
                    "",
                    "function Invoke-Parse {",
                    "    param([string]$Value)",
                    "    return $Value.Trim()",
                    "}",
                    "",
                ]
            ),
        )
        repo.index.add(["src/order.ts", "scripts/review.ps1"])
        repo.index.commit("insert typescript and move powershell")

        diffs = SemanticDiffer().diff_commit(
            repo_path=repo.working_tree_dir,
            old_ref="HEAD~1",
            new_ref="HEAD",
        )

        by_file = {diff.new_filename: diff for diff in diffs}
        assert set(by_file) == {"src/order.ts", "scripts/review.ps1"}
        assert "enrichOrder" in _semantic_labels(
            by_file["src/order.ts"],
            ChangeType.ADDITION,
        )
        assert "enrichOrder" not in _semantic_labels(
            by_file["src/order.ts"],
            ChangeType.MOVE,
        )
        moved_powershell = _semantic_labels(
            by_file["scripts/review.ps1"],
            ChangeType.MOVE,
        )
        assert {"Invoke-Format", "Invoke-Parse"} & moved_powershell

    def test_commit_wide_cross_file_move_uses_real_local_git_history(self, tmp_path):
        from intentdiff.core.commit_differ import CommitDiffer

        repo = _make_repo(tmp_path)
        _commit_file(
            repo,
            "src/order.ts",
            "\n".join(
                [
                    "export function parseOrder(input: string): number {",
                    "    return Number.parseInt(input, 10);",
                    "}",
                    "",
                    "export function formatOrder(value: number): string {",
                    "    return value.toFixed(2);",
                    "}",
                    "",
                ]
            ),
            "initial order helpers",
        )
        _write(
            repo,
            "src/order.ts",
            "\n".join(
                [
                    "export function parseOrder(input: string): number {",
                    "    return Number.parseInt(input, 10);",
                    "}",
                    "",
                ]
            ),
        )
        _write(
            repo,
            "src/format.ts",
            "\n".join(
                [
                    "export function formatOrder(value: number): string {",
                    "    return value.toFixed(2);",
                    "}",
                    "",
                ]
            ),
        )
        repo.index.add(["src/order.ts", "src/format.ts"])
        repo.index.commit("move format helper to new module")

        commit_diff = CommitDiffer().diff_commit(
            repo_path=repo.working_tree_dir,
            old_ref="HEAD~1",
            new_ref="HEAD",
        )

        assert {diff.new_filename for diff in commit_diff.file_diffs} == {
            "src/order.ts",
            "src/format.ts",
        }
        move_changes = [
            change
            for change in commit_diff.cross_file_changes
            if change.change_type == ChangeType.MOVE_TO_MODULE
        ]
        assert [(change.symbol_name, change.old_file, change.new_file) for change in move_changes] == [
            ("formatOrder", "src/order.ts", "src/format.ts")
        ]


# ---------------------------------------------------------------------------
# CLI git command — commit-wide (no FILE argument)
# ---------------------------------------------------------------------------


class TestCliGitCommitWide:
    def test_commit_wide_diff_runs_without_file(self, tmp_path, monkeypatch):
        """'git --old HEAD~1 --new HEAD' (no FILE, cwd inside repo) runs a commit-wide diff."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "main.py", "def f(): pass\n", "v1")
        _commit_file(repo, "main.py", "def f(): return 1\n", "v2")

        monkeypatch.chdir(tmp_path)

        from intentdiff.cli import _build_parser, _cmd_git

        captured = []
        monkeypatch.setattr(
            "intentdiff.cli._shared._render",
            lambda diff, fmt, output, fuel=None: captured.append(diff),
        )

        parser = _build_parser()
        # Pass explicit refs so the diff covers the committed change, not the working tree.
        args = parser.parse_args(["git", "--old", "HEAD~1", "--new", "HEAD"])
        assert args.repo == "."
        assert args.file is None

        _cmd_git(args)
        assert len(captured) >= 1

    def test_cli_hint_for_ref_looking_repo_path(self, tmp_path, monkeypatch, capsys):
        """When REPO looks like a branch name, the error hints about --new."""
        import sys

        from intentdiff.cli import _build_parser, _cmd_git

        parser = _build_parser()
        # Simulate: git --old HEAD -- main   (user typo: '-- main' not '--new main')
        args = parser.parse_args(["git", "--old", "HEAD", "main"])
        # 'main' becomes the FILE arg (one positional → file="main", repo=".")
        # For repo='.', it must be inside a git repo. Use a non-repo tmp dir.
        monkeypatch.chdir(tmp_path)
        # tmp_path is not a git repo, so this should fail with InvalidGitRepositoryError
        with pytest.raises(SystemExit) as exc_info:
            _cmd_git(args)
        assert exc_info.value.code == 1

    def test_cli_no_such_path_hint(self, tmp_path, monkeypatch, capsys):
        """'git new main' → error mentions --new hint when 'new' is a non-path word."""
        import sys

        from intentdiff.cli import _build_parser, _cmd_git

        parser = _build_parser()
        # Two positionals: repo="new", file="main"
        args = parser.parse_args(["git", "new", "main"])
        assert args.repo == "new"

        with pytest.raises(SystemExit) as exc_info:
            _cmd_git(args)
        assert exc_info.value.code == 1
