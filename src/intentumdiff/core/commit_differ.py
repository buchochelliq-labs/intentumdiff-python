"""
intentumdiff.core.commit_differ
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``CommitDiffer`` — per-commit semantic diff with cross-file analysis.

This class extends the single-file ``SemanticDiffer`` to work at the commit
level:

1. Runs ``SemanticDiffer.diff_commit()`` to get per-file ``SemanticDiff``
   objects for every changed file.
2. Parses all changed files (old and new versions) into ``SemanticNode``
   trees to build two ``SemanticIndex`` objects.
3. Calls ``detect_cross_file_changes()`` (Rust core) to detect
   ``MOVE_TO_MODULE`` / ``SPLIT_MODULE`` / ``CROSS_FILE_RENAME`` changes.
4. Returns a ``CommitDiff`` with both the per-file diffs and the cross-file
   changes.

Usage::

    from intentumdiff import CommitDiffer, DiffConfig

    differ = CommitDiffer(DiffConfig(detect_refactorings=True))
    commit_diff = differ.diff_commit("/path/to/repo", "HEAD~1", "HEAD")

    for file_diff in commit_diff.file_diffs:
        print(file_diff.new_filename, file_diff.has_semantic_changes)

    for cross_change in commit_diff.cross_file_changes:
        print(cross_change.change_type, cross_change.symbol_name)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from intentumdiff.core.models import CommitDiff, CrossFileChange, DiffConfig, SemanticDiff
from intentumdiff.differ import SemanticDiffer
from intentumdiff.analysis.cross_file import detect_cross_file_changes
from intentumdiff.core.index import SemanticIndex
from intentumdiff.plugins.exceptions import PluginError, PluginNotFoundError
from intentumdiff.sources.git_source import iter_changed_sources
from intentumdiff.vcs.base import VcsBackend

if TYPE_CHECKING:
    from intentumdiff.core.models import SemanticNode
    from intentumdiff.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


@dataclass
class FileDiffResult:
    """A single per-file diff plus the raw contents needed for index building."""

    file_diff: SemanticDiff
    old_path: str
    new_path: str
    old_content: str
    new_content: str


@dataclass
class FileDiffError:
    """A per-file diff that could not be produced (no parser or pipeline failure)."""

    old_path: str
    new_path: str
    reason: str
    kind: str  # "no_parser" | "pipeline_error"


class CommitDiffer:
    """
    Commit-level semantic differ with cross-file change detection.

    Parameters
    ----------
    config:
        Optional ``DiffConfig`` shared with the underlying ``SemanticDiffer``.
    registry:
        Optional pre-built ``PluginRegistry``.  Useful in test environments.
    """

    def __init__(
        self,
        config: DiffConfig | None = None,
        registry: "PluginRegistry | None" = None,
    ) -> None:
        self._differ = SemanticDiffer(config=config, registry=registry)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diff_commit(
        self,
        repo_path: str | os.PathLike[str] = ".",
        old_ref: str = "HEAD",
        new_ref: str = "",
        *,
        backend: VcsBackend | None = None,
    ) -> CommitDiff:
        """
        Run a full semantic diff for a commit.

        Parameters
        ----------
        repo_path:
            Path to (any directory inside) the git repository.  Ignored when
            *backend* is provided.
        old_ref:
            Old VCS ref (git SHA, SVN revision, Perforce changelist, …).
            Defaults to ``"HEAD"``.
        new_ref:
            New VCS ref.  Defaults to ``""`` (git working tree — diff against
            current unsaved/uncommitted files).  Ignored when *backend* is
            provided with a non-empty *new_ref*.
        backend:
            Optional :class:`~intentumdiff.vcs.base.VcsBackend` instance.
            When provided, all VCS operations are delegated to *backend* and
            the git-specific *repo_path* argument is ignored.  When ``None``
            (default), a :class:`~intentumdiff.vcs.git_backend.GitVcsBackend`
            is created from *repo_path* for backward compatibility.

        Returns
        -------
        CommitDiff
            Per-file diffs + cross-file semantic changes.
        """
        if backend is not None:
            return self._diff_with_backend(backend, old_ref, new_ref)

        results, errors, changed_file_count = self._collect_file_diffs(
            iter_changed_sources(repo_path, old_ref, new_ref)
        )

        return self._finalize_commit_diff(
            results=results,
            errors=errors,
            changed_file_count=changed_file_count,
            old_ref=old_ref,
            new_ref=new_ref,
        )

    def iter_file_diffs(
        self,
        repo_path: str | os.PathLike[str] = ".",
        old_ref: str = "HEAD",
        new_ref: str = "",
    ) -> Iterator[FileDiffResult | FileDiffError]:
        """
        Yield per-file diffs one at a time for progressive/streaming review.

        Yields each changed source's :class:`FileDiffResult` (on success)
        or :class:`FileDiffError` (when no parser is available or the pipeline
        failed). Cross-file analysis is NOT performed here — callers that need
        a complete :class:`CommitDiff` should collect the results and pass them
        to :meth:`finalize_commit_diff`.

        Always uses the git backend (the ``backend`` parameter of
        :meth:`diff_commit` is not supported by this streaming entry point).
        """
        from intentumdiff.content_type import is_text_bytes

        for source in iter_changed_sources(repo_path, old_ref, new_ref):
            old_content, new_content, old_path, new_path, staging_status = source
            # Backstop: primary content-based routing happens at the git read
            # boundary (magic-byte detection); the same Rust detector catches any
            # binary that reaches the streaming path through another route,
            # before it explodes the text parser.
            if not is_text_bytes(new_content.encode("utf-8")) or not is_text_bytes(old_content.encode("utf-8")):
                logger.debug("Skipping %r — binary/non-text asset", old_path)
                continue
            try:
                file_diff = self._differ._run_pipeline(
                    old_content, new_content, old_path, None, new_filename=new_path
                )
                if staging_status is not None:
                    file_diff = file_diff.model_copy(
                        update={"staging_status": staging_status}
                    )
                yield FileDiffResult(
                    file_diff=file_diff,
                    old_path=old_path,
                    new_path=new_path or old_path,
                    old_content=old_content,
                    new_content=new_content,
                )
            except PluginNotFoundError:
                logger.debug("Skipping %r — no parser available", old_path)
                yield FileDiffError(
                    old_path=old_path,
                    new_path=new_path or old_path,
                    reason="no parser available",
                    kind="no_parser",
                )
            except (PluginError, ValueError, RuntimeError) as exc:
                logger.warning("Failed to diff %r: %s", old_path, exc)
                yield FileDiffError(
                    old_path=old_path,
                    new_path=new_path or old_path,
                    reason=str(exc),
                    kind="pipeline_error",
                )

    def finalize_commit_diff(
        self,
        results: list[FileDiffResult],
        errors: list[FileDiffError],
        *,
        old_ref: str,
        new_ref: str,
        repo_path: str | os.PathLike[str] | None = None,
    ) -> CommitDiff:
        """
        Build a complete :class:`CommitDiff` from streamed per-file results.

        Preserves engine-produced file evidence, runs cross-file analysis, and assembles the
        terminal ``CommitDiff``. This is the counterpart to
        :meth:`iter_file_diffs`.
        """
        changed_file_count = len(results) + len(errors)
        return self._finalize_commit_diff(
            results=results,
            errors=errors,
            changed_file_count=changed_file_count,
            old_ref=old_ref,
            new_ref=new_ref,
        )

    def _collect_file_diffs(
        self,
        sources: Iterator[tuple[str, str, str, str, object]],
    ) -> tuple[list[FileDiffResult], list[FileDiffError], int]:
        """Run the per-file pipeline over an iterator of changed sources."""
        results: list[FileDiffResult] = []
        errors: list[FileDiffError] = []
        changed_file_count = 0
        for old_content, new_content, old_path, new_path, staging_status in sources:
            changed_file_count += 1
            try:
                file_diff = self._differ._run_pipeline(
                    old_content, new_content, old_path, None, new_filename=new_path
                )
                if staging_status is not None:
                    file_diff = file_diff.model_copy(
                        update={"staging_status": staging_status}
                    )
                results.append(
                    FileDiffResult(
                        file_diff=file_diff,
                        old_path=old_path,
                        new_path=new_path or old_path,
                        old_content=old_content,
                        new_content=new_content,
                    )
                )
            except PluginNotFoundError:
                logger.debug("Skipping %r — no parser available", old_path)
                errors.append(
                    FileDiffError(
                        old_path=old_path,
                        new_path=new_path or old_path,
                        reason="no parser available",
                        kind="no_parser",
                    )
                )
            except (PluginError, ValueError, RuntimeError) as exc:
                logger.warning("Failed to diff %r: %s", old_path, exc)
                errors.append(
                    FileDiffError(
                        old_path=old_path,
                        new_path=new_path or old_path,
                        reason=str(exc),
                        kind="pipeline_error",
                    )
                )
        return results, errors, changed_file_count

    def _finalize_commit_diff(
        self,
        *,
        results: list[FileDiffResult],
        errors: list[FileDiffError],
        changed_file_count: int,
        old_ref: str,
        new_ref: str,
    ) -> CommitDiff:
        """Assemble the terminal CommitDiff from per-file results + errors."""
        file_diffs = [result.file_diff for result in results]
        parse_errors = [
            f"{error.old_path}: {error.reason}"
            for error in errors
            if error.kind == "pipeline_error"
        ]

        self._raise_if_all_parsers_failed(changed_file_count, file_diffs, parse_errors)

        # Build semantic indexes for changed files only.
        old_file_contents = [
            (result.old_path, result.file_diff.language, result.old_content)
            for result in results
        ]
        new_file_contents = [
            (result.new_path, result.file_diff.language, result.new_content)
            for result in results
        ]
        cross_file_changes: list[CrossFileChange] = []
        if old_file_contents:
            old_index = self._build_index(old_file_contents)
            new_index = self._build_index(new_file_contents)
            if old_index is not None and new_index is not None:
                cross_file_changes = self._detect_cross_file(old_index, new_index)

        return CommitDiff(
            old_ref=old_ref,
            new_ref=new_ref,
            guardrail_violations=[
                violation
                for file_diff in file_diffs
                for violation in file_diff.guardrail_violations
            ],
            file_diffs=file_diffs,
            cross_file_changes=cross_file_changes,
            parse_errors=parse_errors,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _diff_with_backend(
        self,
        backend: VcsBackend,
        old_ref: str,
        new_ref: str,
    ) -> CommitDiff:
        """
        Run a full semantic diff using an arbitrary :class:`VcsBackend`.

        Called by :meth:`diff_commit` when a *backend* is provided.  The
        gitignore pre-pass is skipped because it is git-specific.
        """
        changed_files = backend.list_changed_files(old_ref, new_ref)

        file_diffs = []
        parse_errors: list[str] = []
        old_file_contents: list[tuple[str, str, str]] = []
        new_file_contents: list[tuple[str, str, str]] = []
        changed_file_count = 0

        for cf in changed_files:
            if cf.is_binary:
                continue
            changed_file_count += 1

            old_path = cf.old_path or cf.new_path
            new_path = cf.new_path or cf.old_path

            if old_path is None:
                continue

            old_content = backend.get_blob(old_path, old_ref) if cf.old_path else ""
            new_content = backend.get_blob(new_path, new_ref) if cf.new_path else ""  # type: ignore[arg-type]

            try:
                file_diff = self._differ._run_pipeline(
                    old_content, new_content, old_path, None, new_filename=new_path
                )
                file_diffs.append(file_diff)
                old_file_contents.append((old_path, file_diff.language, old_content))
                new_file_contents.append(
                    (new_path or old_path, file_diff.language, new_content)
                )
            except PluginNotFoundError:
                logger.debug("Skipping %r — no parser available", old_path)
            except (PluginError, ValueError, RuntimeError) as exc:
                logger.warning("Failed to diff %r: %s", old_path, exc)
                parse_errors.append(f"{old_path}: {exc}")

        self._raise_if_all_parsers_failed(changed_file_count, file_diffs, parse_errors)

        cross_file_changes: list[CrossFileChange] = []
        if old_file_contents:
            old_index = self._build_index(old_file_contents)
            new_index = self._build_index(new_file_contents)
            if old_index is not None and new_index is not None:
                cross_file_changes = self._detect_cross_file(old_index, new_index)

        return CommitDiff(
            old_ref=old_ref,
            new_ref=new_ref,
            guardrail_violations=[
                violation
                for file_diff in file_diffs
                for violation in file_diff.guardrail_violations
            ],
            file_diffs=file_diffs,
            cross_file_changes=cross_file_changes,
            parse_errors=parse_errors,
        )

    def _raise_if_all_parsers_failed(
        self,
        changed_file_count: int,
        file_diffs: list,
        parse_errors: list[str],
    ) -> None:
        """Fail loudly when changed files exist but no parser plugin loaded."""
        if changed_file_count <= 0 or file_diffs or parse_errors:
            return
        summary_fn = getattr(self._differ._registry, "parser_load_failure_summary", None)
        if not callable(summary_fn):
            return
        summary = summary_fn()
        if summary:
            raise RuntimeError(summary)

    def _build_index(
        self, file_contents: list[tuple[str, str, str]]
    ) -> SemanticIndex | None:
        """
        Build a ``SemanticIndex`` from a list of (filename, language, content)
        triples.

        The symbol/reference tables are built by the Rust core
        (index-engine-lib) inside ``SemanticIndex.build()``. Returns ``None`` if
        no files could be parsed.
        """
        index = SemanticIndex()
        for filename, language, content in file_contents:
            tree = self._parse_to_tree(filename, language, content)
            if tree is not None:
                index.add_tree(filename, language, tree)

        if not index._files:  # type: ignore[attr-defined]  # pylint: disable=protected-access
            return None

        index.build()
        return index

    def _detect_cross_file(
        self, old_index: SemanticIndex, new_index: SemanticIndex
    ) -> list[CrossFileChange]:
        """Detect cross-file changes between two indexes via the Rust core."""
        return detect_cross_file_changes(old_index, new_index)

    def _parse_to_tree(
        self, filename: str, language: str, content: str
    ) -> "SemanticNode | None":
        """
        Parse *content* to a SemanticNode tree.

        Re-uses the ``SemanticDiffer`` parser pipeline so both FullParse and
        host-CST parser plugins can participate in commit-wide symbol indexing.
        """
        # Match the existing source-diff and native Rust empty-side contract. Some
        # parsers reject empty input; that is not a missing symbol-table failure.
        if not content:
            from intentumdiff._differ_presentation import _empty_semantic_tree
            return _empty_semantic_tree(language)
        try:
            tree, _language = self._differ.parse(
                content,
                filename,
                language_hint=language,
            )
            return tree
        except PluginNotFoundError:
            return None
