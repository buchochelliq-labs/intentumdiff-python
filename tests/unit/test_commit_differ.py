"""
tests/unit/test_commit_differ.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for ``CommitDiffer`` (``intentumdiff.core.commit_differ``).

All git interactions and the underlying ``SemanticDiffer._run_pipeline()`` are
mocked so the tests run without a real repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from intentumdiff.core.commit_differ import CommitDiffer
from intentumdiff.core.index import SemanticIndex
from intentumdiff.core.models import (
    ChangeType,
    CommitDiff,
    DiffConfig,
    NodePosition,
    SemanticDiff,
    SemanticNode,
)
from intentumdiff.differ import SemanticDiffer
from intentumdiff.plugins.exceptions import PluginFuelExhausted
from intentumdiff.rust_core import RustCoreBatchDiffsAttempt, RustCoreCommitJsonAttempt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos() -> NodePosition:
    return NodePosition(start_line=0, start_col=0, end_line=5, end_col=0)


def _fn(id: str, label: str) -> SemanticNode:
    return SemanticNode(
        id=id,
        node_type="function_definition",
        label=label,
        position=_pos(),
        structural_hash=f"h-{id}",
    )


def _style_only_diff(old: str = "a.py", new: str = "a.py") -> SemanticDiff:
    return SemanticDiff.style_only(old, new, "python")


def _semantic_diff(
    old: str = "a.py",
    new: str = "b.py",
    language: str = "python",
) -> SemanticDiff:
    return SemanticDiff(
        old_filename=old,
        new_filename=new,
        language=language,
        changes=[],
        has_semantic_changes=True,
        is_style_only=False,
    )


def _built_index_for_commit(*entries: tuple[str, str, SemanticNode]) -> SemanticIndex:
    idx = SemanticIndex()
    for filename, language, tree in entries:
        idx.add_tree(filename, language, tree)
    idx.build()
    return idx


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_construction(self):
        cd = CommitDiffer()
        assert cd._differ is not None

    def test_accepts_config(self):
        config = DiffConfig(detect_refactorings=False)
        cd = CommitDiffer(config=config)
        assert cd._differ._config.detect_refactorings is False


# ---------------------------------------------------------------------------
# diff_commit — happy path
# ---------------------------------------------------------------------------


class TestSemanticDifferParallelCommit:
    @contextmanager
    def _patch_sources(self, changed_files: list[tuple]):
        # Neutralise BOTH source collectors. The certified working-tree path tries
        # collect_working_tree_python_sources_fast first; since #98/A2.4.3 that shells
        # git directly (no longer via the mocked git.Repo), so it must be patched to
        # None to fall back to the mocked iter_changed_sources — otherwise it runs real
        # git against the test's cwd.
        with (
            patch(
                "intentumdiff.sources.git_source.collect_working_tree_python_sources_fast",
                return_value=None,
            ),
            patch(
                "intentumdiff.sources.git_source.iter_changed_sources",
                return_value=iter(changed_files),
            ),
        ):
            yield

    def _patch_git_inputs(self, changed_files: list[tuple]):
        return (
            patch("git.Repo", return_value=MagicMock()),
            self._patch_sources(changed_files),
        )

    def test_parallel_commit_uses_worker_local_registries(self):
        differ = SemanticDiffer(DiffConfig(parallel=2, experimental_rust_core=False))
        barrier = threading.Barrier(2)
        registry_ids: list[int] = []
        seen_lock = threading.Lock()

        def fake_pipeline(self, old, new, filename, language_hint, **kwargs):
            barrier.wait(timeout=5)
            with seen_lock:
                registry_ids.append(id(self._registry))
            return _semantic_diff(filename, kwargs.get("new_filename") or filename)

        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
        ]
        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch.object(
                SemanticDiffer,
                "_run_pipeline",
                fake_pipeline,
            ),
        ):
            results = differ.diff_commit(".")

        assert [result.new_filename for result in results] == ["a.py", "b.py"]
        assert len(set(registry_ids)) == 2
        assert id(differ._registry) not in registry_ids

    def test_parallel_commit_preserves_source_order(self):
        differ = SemanticDiffer(DiffConfig(parallel=2, experimental_rust_core=False))

        def fake_pipeline(self, old, new, filename, language_hint, **kwargs):
            if filename == "a.py":
                time.sleep(0.05)
            return _semantic_diff(filename, kwargs.get("new_filename") or filename)

        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
        ]
        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch.object(
                SemanticDiffer,
                "_run_pipeline",
                fake_pipeline,
            ),
        ):
            results = differ.diff_commit(".")

        assert [result.new_filename for result in results] == ["a.py", "b.py"]

    def test_parallel_commit_skips_plugin_not_found_like_sequential(self):
        from intentumdiff.plugins.exceptions import PluginNotFoundError

        differ = SemanticDiffer(DiffConfig(parallel=2, experimental_rust_core=False))

        def fake_pipeline(self, old, new, filename, language_hint, **kwargs):
            if filename == "unknown.xyz":
                raise PluginNotFoundError("no parser")
            return _semantic_diff(filename, kwargs.get("new_filename") or filename)

        changed_files = [
            ("old unknown", "new unknown", "unknown.xyz", "unknown.xyz", None),
            ("old good", "new good", "good.py", "good.py", None),
        ]
        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch.object(
                SemanticDiffer,
                "_run_pipeline",
                fake_pipeline,
            ),
        ):
            results = differ.diff_commit(".")

        assert [result.new_filename for result in results] == ["good.py"]

    def test_phase_profile_records_source_collection_once(self):
        differ = SemanticDiffer(
            DiffConfig(parallel=2, profile_phases=True, experimental_rust_core=False)
        )

        def fake_pipeline(self, old, new, filename, language_hint, **kwargs):
            result = _semantic_diff(filename, kwargs.get("new_filename") or filename)
            return self._attach_phase_timings(result, kwargs["_profiler"])

        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
        ]
        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch.object(
                SemanticDiffer,
                "_run_pipeline",
                fake_pipeline,
            ),
        ):
            results = differ.diff_commit(".")

        phase_names = [
            phase["name"]
            for result in results
            for phase in result.metadata["phase_timings"]["phases"]
        ]
        assert phase_names.count("source_collection") == 1
        assert "source_loading" not in phase_names

    def test_commit_uses_single_rust_batch_for_certified_python_files(self):
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True, profile_phases=True))
        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
        ]
        batch_calls: list[dict[str, Any]] = []

        def fake_batch_diffs(**kwargs):
            batch_calls.append(kwargs)
            return RustCoreBatchDiffsAttempt(
                diffs={
                    0: _semantic_diff("a.py", "a.py"),
                    1: _semantic_diff("b.py", "b.py"),
                },
                batch_metadata={"batch_size": 2},
                adapter_phase_timings=[
                    {
                        "name": "rust_core_request_json_encode",
                        "duration_ms": 1.0,
                        "shared": True,
                    },
                    {
                        "name": "rust_core_batch_execution",
                        "duration_ms": 9.0,
                        "shared": True,
                    },
                    {
                        "name": "rust_core_response_validation",
                        "duration_ms": 2.0,
                        "shared": True,
                    },
                ],
                backend_version="0.4.0",
            )

        def fail_pipeline(self, old, new, filename, language_hint, **kwargs):
            raise AssertionError("Python pipeline should not run for Rust batch successes")

        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.differ.try_rust_core_batch_diffs",
                side_effect=fake_batch_diffs,
            ),
            patch.object(
                SemanticDiffer,
                "_run_pipeline",
                fail_pipeline,
            ),
        ):
            results = differ.diff_commit(".")

        assert [result.new_filename for result in results] == ["a.py", "b.py"]
        assert len(batch_calls) == 1
        assert len(batch_calls[0]["files"]) == 2
        assert batch_calls[0]["parallel"] is True
        assert batch_calls[0]["max_workers"] == min(os.cpu_count() or 1, 2)
        for result in results:
            batch_metadata = result.metadata["rust_core_batch"]
            assert batch_metadata["rust_core_batch_parallel_auto"] is True
            assert batch_metadata["parallel_workers"] == min(os.cpu_count() or 1, 2)
            assert batch_metadata["batch_size"] == 2
            assert batch_metadata["fallback_count"] == 0
            phase_names = [phase["name"] for phase in result.metadata["phase_timings"]["phases"]]
            assert "source_collection" in phase_names
            assert "rust_core_batch_execution" in phase_names
            assert "rust_core_request_json_encode" in phase_names
            assert "rust_core_response_validation" in phase_names
            assert "commit_result_merge" in phase_names
            adapter_phase_names = [
                phase["name"]
                for phase in result.metadata["rust_core_batch"]["adapter_phase_timings"]
            ]
            assert adapter_phase_names[0] == "rust_core_commit_source_filtering"
            assert "rust_core_request_json_encode" in adapter_phase_names

    def test_commit_rust_batch_fallback_reruns_with_rust_core_enabled(self):
        # Reformulated for the transitional-layer retirement (issue #57 payoff, stage
        # 4b): batch-declined files used to re-run through a python-pipeline differ
        # with the Rust core OFF ("without retrying rust"). The python pipeline is
        # deleted — a rust-off re-run now means the token-fallback kill switch, which
        # turned a comment-only commit into a token ADDITION. Declined files re-run
        # with the Rust core ON: the single-file batch re-attempt is one cheap native
        # call and the per-stage finalize tier serves the review.
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True))
        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
        ]
        pipeline_rust_flags: list[bool] = []

        def fake_batch_diffs(**kwargs):
            return RustCoreBatchDiffsAttempt(
                diffs={0: _semantic_diff("a.py", "a.py")},
                fallback_reasons={1: "forced fallback"},
                batch_metadata={"fallback_count": 1},
                backend_version="0.4.0",
            )

        def fake_pipeline(self, old, new, filename, language_hint, **kwargs):
            pipeline_rust_flags.append(self._config.experimental_rust_core)
            return _semantic_diff(filename, kwargs.get("new_filename") or filename)

        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.differ.try_rust_core_batch_diffs",
                side_effect=fake_batch_diffs,
            ),
            patch.object(
                SemanticDiffer,
                "_run_pipeline",
                fake_pipeline,
            ),
        ):
            results = differ.diff_commit(".")

        assert [result.new_filename for result in results] == ["a.py", "b.py"]
        assert pipeline_rust_flags == [True]

    def test_commit_rust_batch_integer_parallel_caps_workers(self):
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True, parallel=3))
        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
            ("old c", "new c", "c.py", "c.py", None),
            ("old d", "new d", "d.py", "d.py", None),
        ]
        batch_calls: list[dict[str, Any]] = []

        def fake_batch_diffs(**kwargs):
            batch_calls.append(kwargs)
            return RustCoreBatchDiffsAttempt(
                diffs={
                    0: _semantic_diff("a.py", "a.py"),
                    1: _semantic_diff("b.py", "b.py"),
                    2: _semantic_diff("c.py", "c.py"),
                    3: _semantic_diff("d.py", "d.py"),
                },
                batch_metadata={"batch_size": 4, "parallel_workers": 3},
                backend_version="0.4.0",
            )

        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.differ.try_rust_core_batch_diffs",
                side_effect=fake_batch_diffs,
            ),
        ):
            results = differ.diff_commit(".")

        assert len(results) == 4
        assert len(batch_calls) == 1
        assert batch_calls[0]["parallel"] is True
        assert batch_calls[0]["max_workers"] == 3
        assert all(
            result.metadata["rust_core_batch"]["rust_core_batch_parallel_auto"] is False
            for result in results
        )

    def test_commit_rust_batch_preserves_staging_status(self):
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True))
        changed_files = [
            ("old a", "new a", "a.py", "a.py", "staged"),
            ("old b", "new b", "b.py", "b.py", None),
        ]

        def fake_batch_diffs(**kwargs):
            return RustCoreBatchDiffsAttempt(
                diffs={
                    0: _semantic_diff("a.py", "a.py"),
                    1: _semantic_diff("b.py", "b.py"),
                },
                batch_metadata={"batch_size": 2},
                backend_version="0.4.0",
            )

        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.differ.try_rust_core_batch_diffs",
                side_effect=fake_batch_diffs,
            ),
        ):
            results = differ.diff_commit(".")

        assert [result.staging_status for result in results] == ["staged", None]

    def test_certified_commit_json_collects_python_batch_without_model_conversion(self):
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True))
        changed_files = [
            ("old a", "new a", "a.py", "a.py", "staged"),
            ("old b", "new b", "b.py", "b.py", None),
        ]
        commit_bytes = json.dumps(
            {
                "old_ref": "HEAD",
                "new_ref": "",
                "guardrail_violations": [],
                "file_diffs": [
                    _semantic_diff("a.py", "a.py").model_dump(mode="json"),
                    _semantic_diff("b.py", "b.py").model_dump(mode="json"),
                ],
                "cross_file_changes": [],
                "parse_errors": [],
            }
        ).encode("utf-8")
        calls: list[dict[str, Any]] = []

        def fake_commit_json(**kwargs):
            calls.append(kwargs)
            return RustCoreCommitJsonAttempt(
                control={"status": "complete", "certification": "python_native_v4kb"},
                commit_diff_json=commit_bytes,
                adapter_phase_timings=[
                    {
                        "name": "rust_core_commit_pyo3_call",
                        "duration_ms": 3.0,
                        "shared": True,
                        "summary_exclude": True,
                    }
                ],
                backend_version="0.4.0",
            )

        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.differ.try_rust_core_working_tree_commit_json",
                return_value=RustCoreCommitJsonAttempt(fallback_reason="unavailable: test"),
            ),
            patch(
                "intentumdiff.differ.try_rust_core_commit_json",
                side_effect=fake_commit_json,
            ),
        ):
            attempt = differ._diff_commit_certified_json(".")

        assert attempt.used is True
        assert attempt.commit_diff_json == commit_bytes
        assert calls[0]["old_ref"] == "HEAD"
        assert calls[0]["new_ref"] == ""
        assert calls[0]["parallel"] is True
        assert calls[0]["max_workers"] == min(os.cpu_count() or 1, 2)
        assert [file["staging_status"] for file in calls[0]["files"][:1]] == ["staged"]
        assert calls[0]["files"][0]["parser_plugin_id"] == "python"
        phase_names = [phase["name"] for phase in attempt.adapter_phase_timings]
        assert phase_names[0] == "source_collection"
        assert "rust_core_commit_source_filtering" in phase_names
        assert "rust_core_commit_pyo3_call" in phase_names
        assert attempt.control["source_collection_ms"] >= 0
        assert attempt.control["source_filter_ms"] >= 0

    def test_certified_commit_json_falls_back_for_mixed_languages_without_backend_call(
        self, monkeypatch
    ):
        # This asserts the NON-strict decline-return contract (the certified-JSON fast
        # path returns a fallback attempt for a mixed-language batch so the caller can
        # re-run natively). Under the RUST_ONLY gate the same decline instead RAISES —
        # that strict behaviour is covered by
        # test_certified_commit_json_strict_rust_gate_blocks_unavailable_backend_fallback —
        # so pin this test to the non-strict mode regardless of the ambient suite env.
        from intentumdiff.differ import STRICT_RUST_GATE_ENV

        monkeypatch.delenv(STRICT_RUST_GATE_ENV, raising=False)
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True))
        changed_files = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old go", "new go", "main.go", "main.go", None),
        ]

        def fail_commit_json(**kwargs):
            raise AssertionError("mixed-language commit should not reach Rust JSON fast path")

        repo_patch, sources_patch = self._patch_git_inputs(changed_files)
        with (
            repo_patch,
            sources_patch,
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.differ.try_rust_core_working_tree_commit_json",
                return_value=RustCoreCommitJsonAttempt(fallback_reason="unavailable: test"),
            ),
            patch(
                "intentumdiff.differ.try_rust_core_commit_json",
                side_effect=fail_commit_json,
            ),
        ):
            attempt = differ._diff_commit_certified_json(".")

        assert attempt.used is False
        assert attempt.fallback_reason == (
            "certified commit JSON requires all changed files to be Python"
        )

    def test_certified_commit_json_strict_rust_gate_blocks_python_batch_skip(self, monkeypatch):
        from intentumdiff.differ import STRICT_RUST_GATE_ENV

        monkeypatch.setenv(STRICT_RUST_GATE_ENV, "1")
        differ = SemanticDiffer(
            DiffConfig(experimental_rust_core=True, extra_grammars={"python": "custom.py"})
        )

        with pytest.raises(
            RuntimeError,
            match=(
                r"Rust-only engine gate prevented fallback: "
                "custom Python grammar requires Python pipeline"
            ),
        ):
            differ._diff_commit_certified_json(".")

    def test_certified_commit_json_strict_rust_gate_blocks_working_tree_fallback(self, monkeypatch):
        from intentumdiff.differ import STRICT_RUST_GATE_ENV

        monkeypatch.setenv(STRICT_RUST_GATE_ENV, "1")
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True))

        with (
            patch(
                "intentumdiff.differ.try_rust_core_working_tree_commit_json",
                return_value=RustCoreCommitJsonAttempt(fallback_reason="backend unavailable"),
            ),
            pytest.raises(
                RuntimeError,
                match=r"Rust-only engine gate prevented fallback: backend unavailable",
            ),
        ):
            differ._diff_commit_certified_json(".")

    def test_certified_commit_json_strict_rust_gate_blocks_unavailable_backend_fallback(
        self, monkeypatch
    ):
        from intentumdiff.differ import STRICT_RUST_GATE_ENV

        monkeypatch.setenv(STRICT_RUST_GATE_ENV, "1")
        differ = SemanticDiffer(DiffConfig(experimental_rust_core=True))

        with (
            patch(
                "intentumdiff.differ.try_rust_core_working_tree_commit_json",
                return_value=RustCoreCommitJsonAttempt(fallback_reason="unavailable: test"),
            ),
            patch(
                "intentumdiff.plugins.builtins.python_parser_entry",
                return_value="python_parser.wasm",
            ),
            patch(
                "intentumdiff.sources.git_source.iter_changed_sources",
                return_value=iter(
                    [
                        ("old a", "new a", "a.py", "a.py", None),
                        ("old b", "new b", "main.go", "main.go", None),
                    ]
                ),
            ),
            patch(
                "intentumdiff.sources.git_source.collect_working_tree_python_sources_fast",
                return_value=None,
            ),
            pytest.raises(
                RuntimeError,
                match=(
                    r"Rust-only engine gate prevented fallback: "
                    "certified commit JSON requires all changed files to be Python"
                ),
            ),
        ):
            differ._diff_commit_certified_json(".")


class TestDiffCommit:
    def _make_differ(self) -> CommitDiffer:
        cd = CommitDiffer()
        return cd

    def _patch_pipeline(self, cd: CommitDiffer, diffs: list[SemanticDiff]):
        """Patch _run_pipeline to return successive diffs."""
        cd._differ._run_pipeline = MagicMock(side_effect=diffs)

    def _patch_iter_changed_sources(self, changed_files: list[tuple]):
        """Return a context manager that patches iter_changed_sources."""
        return patch(
            "intentumdiff.core.commit_differ.iter_changed_sources",
            return_value=iter(changed_files),
        )

    def _patch_git_repo(self):
        # commit_differ no longer uses GitPython; neutralize the gitignore pre-pass
        # (git-CLI) so diff_commit stays hermetic — the diff data comes from the
        # mocked iter_changed_sources.
        return patch.object(
            CommitDiffer, "_collect_gitignore_state", return_value=(set(), None)
        )

    def test_returns_commit_diff(self):
        cd = self._make_differ()
        diff = _semantic_diff()

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old content", "new content", "a.py", "a.py", None)]
            ):
                cd._differ._run_pipeline = MagicMock(return_value=diff)
                result = cd.diff_commit(".", "HEAD~1", "HEAD")

        assert isinstance(result, CommitDiff)
        assert result.old_ref == "HEAD~1"
        assert result.new_ref == "HEAD"

    def test_file_diffs_populated(self):
        cd = self._make_differ()
        diff = _semantic_diff("a.py", "a.py")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources([("old", "new", "a.py", "a.py", None)]):
                cd._differ._run_pipeline = MagicMock(return_value=diff)
                result = cd.diff_commit(".")

        assert len(result.file_diffs) == 1
        assert result.file_diffs[0] is diff

    def test_parse_error_collected(self):
        cd = self._make_differ()

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources([("old", "new", "broken.py", "broken.py", None)]):
                cd._differ._run_pipeline = MagicMock(side_effect=ValueError("synthetic error"))
                result = cd.diff_commit(".")

        assert any("broken.py" in e for e in result.parse_errors)

    def test_plugin_error_collected_without_aborting_commit_review(self):
        cd = self._make_differ()
        good_diff = _semantic_diff("good.py", "good.py")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [
                    ("old", "new", "fuel.py", "fuel.py", None),
                    ("old", "new", "good.py", "good.py", None),
                ]
            ):
                cd._differ._run_pipeline = MagicMock(
                    side_effect=[
                        PluginFuelExhausted("python-parser", 100_000_000),
                        good_diff,
                    ]
                )
                cd._parse_to_tree = MagicMock(return_value=None)
                result = cd.diff_commit(".")

        assert result.file_diffs == [good_diff]
        assert any("fuel.py" in error and "FUEL_EXCEEDED" in error for error in result.parse_errors)

    def test_plugin_not_found_skips_file(self):
        cd = self._make_differ()
        from intentumdiff.plugins.exceptions import PluginNotFoundError

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "new", "unknown.xyz", "unknown.xyz", None)]
            ):
                cd._differ._run_pipeline = MagicMock(side_effect=PluginNotFoundError("no parser"))
                result = cd.diff_commit(".")

        assert result.file_diffs == []
        assert result.parse_errors == []

    def test_all_parser_load_failure_is_not_reported_as_clean_review(self):
        cd = self._make_differ()
        from intentumdiff.plugins.exceptions import PluginNotFoundError

        cd._differ._registry = SimpleNamespace(
            parser_load_failure_summary=lambda: "No parser plugins could be loaded"
        )

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "new", "pyproject.toml", "pyproject.toml", None)]
            ):
                cd._differ._run_pipeline = MagicMock(
                    side_effect=PluginNotFoundError("unknown", "pyproject.toml")
                )

                with pytest.raises(RuntimeError, match="No parser plugins could be loaded"):
                    cd.diff_commit(".")

    def test_empty_commit_returns_empty_diff(self):
        cd = self._make_differ()

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources([]):
                result = cd.diff_commit(".")

        assert result.file_diffs == []
        assert result.cross_file_changes == []
        assert result.parse_errors == []

    def test_cross_file_changes_detected(self):
        """When a symbol moves between files, MOVE_TO_MODULE should be detected."""
        cd = self._make_differ()

        # Simulate two diffs: function "helper" moved from utils.py → helpers.py
        diff_old = _semantic_diff("utils.py", "utils.py")
        diff_new = _semantic_diff("helpers.py", "helpers.py")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [
                    ("old_utils", "new_utils", "utils.py", "utils.py", None),
                    ("old_helpers", "new_helpers", "helpers.py", "helpers.py", None),
                ]
            ):
                # _run_pipeline is called twice
                cd._differ._run_pipeline = MagicMock(side_effect=[diff_old, diff_new])

                # _parse_to_tree will be used to build indexes; mock it to return
                # trees with the "helper" function in old vs. new file
                fn_old = _fn("1", "helper")
                fn_new = _fn("2", "helper")

                def _fake_parse_to_tree(filename, language, content):
                    # old utils.py had "helper"; new helpers.py has "helper"
                    if content == "old_utils":
                        return fn_old
                    if content == "new_helpers":
                        return fn_new
                    return None  # all other file versions have no relevant symbols

                cd._parse_to_tree = _fake_parse_to_tree

                result = cd.diff_commit(".")

        move_changes = [
            c for c in result.cross_file_changes if c.change_type == ChangeType.MOVE_TO_MODULE
        ]
        assert len(move_changes) == 1
        assert move_changes[0].symbol_name == "helper"

    def test_cross_file_translation_preserves_symbol_metadata(self):
        """The native cross-file path preserves positions, node ids, language,
        and the derived symbol_kind when marshalling each CrossFileChange."""
        cd = self._make_differ()

        old_idx = _built_index_for_commit(("utils.py", "python", _fn("old-helper", "helper")))
        new_idx = _built_index_for_commit(("helpers.py", "python", _fn("new-helper", "helper")))

        changes = cd._detect_cross_file(old_idx, new_idx)

        move = [c for c in changes if c.change_type == ChangeType.MOVE_TO_MODULE]
        assert len(move) == 1
        c = move[0]
        assert c.symbol_name == "helper"
        assert c.old_file == "utils.py"
        assert c.new_file == "helpers.py"
        assert c.old_node_id == "old-helper"
        assert c.new_node_id == "new-helper"
        assert c.old_position is not None and c.old_position.end_line == 5
        assert c.new_position is not None and c.new_position.start_line == 0
        assert c.old_language == "python"
        assert c.new_language == "python"
        assert c.node_type == "function_definition"
        assert c.symbol_kind == "function"

    def test_streaming_iterator_matches_diff_commit(self):
        """iter_file_diffs + finalize_commit_diff must equal diff_commit exactly."""
        cd = self._make_differ()
        diff_a = _semantic_diff("a.py", "a.py")
        diff_b = _semantic_diff("b.py", "b.py")
        sources = [
            ("old a", "new a", "a.py", "a.py", None),
            ("old b", "new b", "b.py", "b.py", None),
        ]

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(list(sources)):
                cd._differ._run_pipeline = MagicMock(side_effect=[diff_a, diff_b])
                direct = cd.diff_commit(".", "HEAD~1", "HEAD")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(list(sources)):
                cd._differ._run_pipeline = MagicMock(side_effect=[diff_a, diff_b])
                streamed = list(cd.iter_file_diffs(".", "HEAD~1", "HEAD"))

        from intentumdiff.core.commit_differ import FileDiffResult

        results = [item for item in streamed if isinstance(item, FileDiffResult)]
        assert len(results) == 2
        finalized = cd.finalize_commit_diff(
            results, [], old_ref="HEAD~1", new_ref="HEAD", repo_path="."
        )
        assert finalized.model_dump_json() == direct.model_dump_json()

    def test_iter_file_diffs_yields_error_for_pipeline_failure(self):
        cd = self._make_differ()

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "new", "bad.py", "bad.py", None)]
            ):
                cd._differ._run_pipeline = MagicMock(side_effect=ValueError("boom"))
                streamed = list(cd.iter_file_diffs("."))

        from intentumdiff.core.commit_differ import FileDiffError

        assert len(streamed) == 1
        assert isinstance(streamed[0], FileDiffError)
        assert streamed[0].kind == "pipeline_error"

    def test_iter_file_diffs_skips_binary_asset_sources(self):
        """Binary/image content must never reach the text pipeline (a PNG fed to
        the generic parser explodes the CST past the plugin output limit)."""
        cd = self._make_differ()
        parsed: list[str] = []

        def fake_pipeline(_old, _new, old_path, _hint, new_filename=None):
            parsed.append(new_filename or old_path)
            return MagicMock()

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources([
                ("\x00png-before", "\x00png-after", "image.png", "image.png", None),
                ("def a():\n    return 1\n", "def a():\n    return 2\n", "code.py", "code.py", None),
            ]):
                cd._differ._run_pipeline = MagicMock(side_effect=fake_pipeline)
                streamed = list(cd.iter_file_diffs("."))

        # The binary image was skipped entirely; only the text file was parsed.
        assert parsed == ["code.py"]
        paths = [getattr(item, "new_path", None) for item in streamed]
        assert "image.png" not in paths
        assert "code.py" in paths


# ---------------------------------------------------------------------------
# _parse_to_tree
# ---------------------------------------------------------------------------


class TestParseToTree:
    def test_returns_none_for_unknown_extension(self):
        cd = CommitDiffer()
        from intentumdiff.plugins.exceptions import PluginNotFoundError

        cd._differ._registry.detect_parser = MagicMock(side_effect=PluginNotFoundError("no parser"))
        result = cd._parse_to_tree("file.unknown", "unknown", "content")
        assert result is None

    def test_returns_none_on_parse_exception(self):
        cd = CommitDiffer()
        cd._differ._registry.detect_parser = MagicMock(side_effect=RuntimeError("boom"))
        result = cd._parse_to_tree("file.py", "python", "def foo(): pass")
        assert result is None


# ---------------------------------------------------------------------------
# _collect_gitignore_state
# ---------------------------------------------------------------------------


class TestCollectGitignoreState:
    """Tests for CommitDiffer._collect_gitignore_state() against real git repos.

    The pre-pass shells to the git CLI (#98, A2.4.3), so these build real two-commit
    repositories (HEAD~1 -> HEAD) rather than mocking the GitPython object model.
    """

    @staticmethod
    def _git(cwd, *args: str) -> None:
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    def _init_repo(self, tmp_path) -> None:
        self._git(tmp_path, "init")
        self._git(tmp_path, "config", "user.email", "t@example.com")
        self._git(tmp_path, "config", "user.name", "T")

    def _commit(self, tmp_path, message: str, *, allow_empty: bool = False) -> None:
        self._git(tmp_path, "add", "-A")
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        self._git(tmp_path, *args)

    def test_no_changes_returns_empty(self, tmp_path):
        cd = CommitDiffer()
        self._init_repo(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n")
        self._commit(tmp_path, "v1")
        self._commit(tmp_path, "v2", allow_empty=True)  # no file changes
        deleted, spec = cd._collect_gitignore_state(str(tmp_path), "HEAD~1", "HEAD")
        assert deleted == set()
        assert spec is None

    def test_deletion_collected(self, tmp_path):
        cd = CommitDiffer()
        self._init_repo(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "old.py").write_text("x = 1\n")
        self._commit(tmp_path, "v1")
        (tmp_path / "src" / "old.py").unlink()
        self._commit(tmp_path, "v2")
        deleted, spec = cd._collect_gitignore_state(str(tmp_path), "HEAD~1", "HEAD")
        assert "src/old.py" in deleted
        assert spec is None

    def test_gitignore_modification_builds_spec(self, tmp_path):
        cd = CommitDiffer()
        self._init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("# initial\n")
        self._commit(tmp_path, "v1")
        (tmp_path / ".gitignore").write_text("*.log\nbuild/\n")
        self._commit(tmp_path, "v2")
        _, spec = cd._collect_gitignore_state(str(tmp_path), "HEAD~1", "HEAD")
        assert spec is not None
        assert spec.match_file("app.log")
        assert spec.match_file("build/output.js")
        assert not spec.match_file("src/main.py")

    def test_gitignore_addition_builds_spec(self, tmp_path):
        cd = CommitDiffer()
        self._init_repo(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n")
        self._commit(tmp_path, "v1")
        (tmp_path / ".gitignore").write_text("dist/\n")
        self._commit(tmp_path, "v2")
        _, spec = cd._collect_gitignore_state(str(tmp_path), "HEAD~1", "HEAD")
        assert spec is not None
        assert spec.match_file("dist/bundle.js")

    def test_subdir_gitignore_prefixes_patterns(self, tmp_path):
        cd = CommitDiffer()
        self._init_repo(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / ".gitignore").write_text("# initial\n")
        self._commit(tmp_path, "v1")
        (tmp_path / "src" / ".gitignore").write_text("*.pyc\n")
        self._commit(tmp_path, "v2")
        _, spec = cd._collect_gitignore_state(str(tmp_path), "HEAD~1", "HEAD")
        assert spec is not None
        assert spec.match_file("src/foo.pyc")
        # Root-level .pyc should not match (pattern was scoped to src/)
        assert not spec.match_file("foo.pyc")

    def test_exception_returns_empty(self, tmp_path):
        cd = CommitDiffer()
        # A path that isn't a git repo -> resolve_repo_root raises -> caught -> empty.
        deleted, spec = cd._collect_gitignore_state(str(tmp_path), "HEAD~1", "HEAD")
        assert deleted == set()
        assert spec is None


# ---------------------------------------------------------------------------
# gitignore_excluded tagging in diff_commit
# ---------------------------------------------------------------------------


class TestGitignoreExcluded:
    """Integration-style tests: diff_commit tags diffs whose deletion was
    triggered by a .gitignore rule change."""

    def _make_differ(self) -> CommitDiffer:
        return CommitDiffer()

    def _patch_iter_changed_sources(self, changed_files):
        return patch(
            "intentumdiff.core.commit_differ.iter_changed_sources",
            return_value=iter(changed_files),
        )

    def _patch_git_repo(self):
        # commit_differ no longer uses GitPython; neutralize the gitignore pre-pass
        # (git-CLI) so diff_commit stays hermetic — the diff data comes from the
        # mocked iter_changed_sources.
        return patch.object(
            CommitDiffer, "_collect_gitignore_state", return_value=(set(), None)
        )

    def _make_gitignore_state(
        self, cd: CommitDiffer, deleted: set[str], gitignore_content: str | None
    ):
        """Patch _collect_gitignore_state to return controlled values."""
        import pathspec as _ps

        spec = (
            _ps.PathSpec.from_lines("gitignore", gitignore_content.splitlines())
            if gitignore_content
            else None
        )
        cd._collect_gitignore_state = MagicMock(return_value=(deleted, spec))

    def test_deleted_file_matching_new_gitignore_is_tagged(self):
        cd = self._make_differ()
        diff = _semantic_diff("logs/app.log", "logs/app.log")
        self._make_gitignore_state(cd, {"logs/app.log"}, "*.log\n")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "", "logs/app.log", "logs/app.log", None)]
            ):
                cd._differ._run_pipeline = MagicMock(return_value=diff)
                result = cd.diff_commit(".")

        assert len(result.file_diffs) == 1
        assert result.file_diffs[0].gitignore_excluded is True

    def test_deleted_file_not_matching_pattern_not_tagged(self):
        cd = self._make_differ()
        diff = _semantic_diff("src/main.py", "src/main.py")
        self._make_gitignore_state(cd, {"src/main.py"}, "*.log\n")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "", "src/main.py", "src/main.py", None)]
            ):
                cd._differ._run_pipeline = MagicMock(return_value=diff)
                result = cd.diff_commit(".")

        assert result.file_diffs[0].gitignore_excluded is False

    def test_no_gitignore_change_never_tagged(self):
        cd = self._make_differ()
        diff = _semantic_diff("src/main.py", "src/main.py")
        self._make_gitignore_state(cd, {"src/main.py"}, None)  # no gitignore spec

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "", "src/main.py", "src/main.py", None)]
            ):
                cd._differ._run_pipeline = MagicMock(return_value=diff)
                result = cd.diff_commit(".")

        assert result.file_diffs[0].gitignore_excluded is False

    def test_non_deleted_files_not_tagged(self):
        cd = self._make_differ()
        diff = _semantic_diff("src/main.py", "src/main.py")
        # File appears in gitignore but it's NOT in deleted_paths (it's a modification)
        self._make_gitignore_state(cd, set(), "*.py\n")

        with self._patch_git_repo() as mock_git:
            mock_git.Repo.return_value = MagicMock()
            with self._patch_iter_changed_sources(
                [("old", "new", "src/main.py", "src/main.py", None)]
            ):
                cd._differ._run_pipeline = MagicMock(return_value=diff)
                result = cd.diff_commit(".")

        assert result.file_diffs[0].gitignore_excluded is False
