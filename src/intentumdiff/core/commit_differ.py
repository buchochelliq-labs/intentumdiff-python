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

        # Pre-pass: collect git-deletion paths and any new .gitignore content so
        # we can tag SemanticDiff objects whose deletion was triggered by a .gitignore
        # rule change rather than actual code removal.
        deleted_paths, gitignore_spec = self._collect_gitignore_state(
            repo_path, old_ref, new_ref
        )

        results, errors, changed_file_count = self._collect_file_diffs(
            iter_changed_sources(repo_path, old_ref, new_ref)
        )

        return self._finalize_commit_diff(
            results=results,
            errors=errors,
            changed_file_count=changed_file_count,
            old_ref=old_ref,
            new_ref=new_ref,
            deleted_paths=deleted_paths,
            gitignore_spec=gitignore_spec,
        )

    def iter_file_diffs(
        self,
        repo_path: str | os.PathLike[str] = ".",
        old_ref: str = "HEAD",
        new_ref: str = "",
    ) -> Iterator[FileDiffResult | FileDiffError]:
        """
        Yield per-file diffs one at a time for progressive/streaming review.

        Performs the gitignore pre-pass (so deletions are classifiable) and
        then yields each changed source's :class:`FileDiffResult` (on success)
        or :class:`FileDiffError` (when no parser is available or the pipeline
        failed). Cross-file analysis is NOT performed here — callers that need
        a complete :class:`CommitDiff` should collect the results and pass them
        to :meth:`finalize_commit_diff`.

        Always uses the git backend (the ``backend`` parameter of
        :meth:`diff_commit` is not supported by this streaming entry point).
        """
        def _looks_binary(text: str) -> bool:
            # Content is decoded with errors="replace"; a NUL byte (valid UTF-8
            # U+0000) survives and reliably marks binary/image assets, as does a
            # high ratio of U+FFFD replacement characters. Feeding such content to
            # a text parser (the generic catch-all) explodes the CST — e.g. a PNG
            # producing >100 MB of output that the plugin host then rejects.
            head = text[:8192]
            if not head:
                return False
            if "\x00" in head:
                return True
            return head.count("�") / len(head) > 0.1

        for source in iter_changed_sources(repo_path, old_ref, new_ref):
            old_content, new_content, old_path, new_path, staging_status = source
            # Backstop: primary content-based routing happens at the git read
            # boundary (magic-byte detection); this NUL-byte check catches any
            # binary that reaches the streaming path through another route,
            # before it explodes the text parser.
            if _looks_binary(new_content) or _looks_binary(old_content):
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

        Applies the gitignore-deletion tagging (when *repo_path* is supplied so
        the pre-pass can run), runs cross-file analysis, and assembles the
        terminal ``CommitDiff``. This is the counterpart to
        :meth:`iter_file_diffs`.
        """
        deleted_paths: set[str] = set()
        gitignore_spec = None
        if repo_path is not None:
            deleted_paths, gitignore_spec = self._collect_gitignore_state(
                repo_path, old_ref, new_ref
            )
        changed_file_count = len(results) + len(errors)
        return self._finalize_commit_diff(
            results=results,
            errors=errors,
            changed_file_count=changed_file_count,
            old_ref=old_ref,
            new_ref=new_ref,
            deleted_paths=deleted_paths,
            gitignore_spec=gitignore_spec,
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
        deleted_paths: set[str] | None = None,
        gitignore_spec=None,
    ) -> CommitDiff:
        """Assemble the terminal CommitDiff from per-file results + errors."""
        file_diffs = [result.file_diff for result in results]
        parse_errors = [
            f"{error.old_path}: {error.reason}"
            for error in errors
            if error.kind == "pipeline_error"
        ]

        self._raise_if_all_parsers_failed(changed_file_count, file_diffs, parse_errors)

        # Tag diffs whose file deletion was caused by a .gitignore rule addition.
        if gitignore_spec is not None and deleted_paths:
            file_diffs = [
                fd.model_copy(update={"gitignore_excluded": True})
                if fd.old_filename in deleted_paths
                and gitignore_spec.match_file(fd.old_filename)
                else fd
                for fd in file_diffs
            ]

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

    def _collect_gitignore_state(
        self,
        repo_path: "str | os.PathLike[str]",
        old_ref: str,
        new_ref: str,
    ) -> "tuple[set[str], object]":
        """
        Lightweight pre-scan of the commit diff.

        Returns a 2-tuple ``(deleted_paths, gitignore_spec)``:

        * ``deleted_paths`` — set of file paths that git marks as deleted (change
          type ``"D"``).  Used later to distinguish genuine deletions from files
          evicted by a ``.gitignore`` rule change.
        * ``gitignore_spec`` — a compiled ``pathspec.PathSpec`` built from the
          *new* content of any ``.gitignore`` file modified in this commit, or
          ``None`` when no ``.gitignore`` changed.  Only root-level and
          subdirectory ``.gitignore`` files that appear in the commit diff are
          considered; ``.git/info/exclude`` is outside the commit tree and is
          therefore ignored.
        """
        deleted_paths: set[str] = set()
        gitignore_spec = None
        try:
            import subprocess

            from intentumdiff.sources.git_source import _parse_name_status_z
            from intentumdiff.vcs.git_cli import resolve_repo_root, run_git_bytes

            root = resolve_repo_root(repo_path)
            # old -> new (or old -> working tree when new_ref is ""); a=old, b=new,
            # matching the former old_commit.diff(None|new_commit).
            args = ["diff", "--name-status", "-z", old_ref]
            if new_ref:
                args.append(new_ref)
            for code, a_path, b_path in _parse_name_status_z(run_git_bytes(root, args)):
                if code[:1] == "D":
                    deleted_paths.add(a_path)

                # Capture the new .gitignore content for root or subdir gitignore files.
                if code[:1] in ("A", "M") and (
                    b_path == ".gitignore" or b_path.endswith("/.gitignore")
                ):
                    raw: str | None = None
                    if new_ref:  # commit-to-commit: read the new blob
                        try:
                            raw = run_git_bytes(
                                root, ["cat-file", "blob", f"{new_ref}:{b_path}"]
                            ).decode("utf-8", errors="replace")
                        except subprocess.CalledProcessError:
                            raw = None
                    else:  # working-tree mode: read from disk
                        from pathlib import Path as _Path
                        disk_gi = _Path(root) / b_path
                        if disk_gi.exists():
                            try:
                                raw = disk_gi.read_text(encoding="utf-8", errors="replace")
                            except OSError:
                                pass
                    if raw is not None:
                        try:
                            import pathspec as _pathspec  # lazy import
                            # For subdirectory gitignore files (e.g. src/.gitignore),
                            # prepend the directory so patterns are matched against
                            # full repo-relative paths.
                            gitignore_dir = b_path[: -len(".gitignore")].rstrip("/")
                            if gitignore_dir:
                                lines = [
                                    f"{gitignore_dir}/{ln}" if ln and not ln.startswith("#")
                                    else ln
                                    for ln in raw.splitlines()
                                ]
                            else:
                                lines = raw.splitlines()
                            gitignore_spec = _pathspec.PathSpec.from_lines("gitignore", lines)
                        except Exception as exc:  # pragma: no cover
                            logger.debug("Failed to parse .gitignore blob: %s", exc)
        except Exception as exc:
            logger.debug("gitignore pre-pass failed: %s", exc)
        return deleted_paths, gitignore_spec

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
            try:
                tree = self._parse_to_tree(filename, language, content)
                if tree is not None:
                    index.add_tree(filename, language, tree)
            except Exception as exc:
                logger.debug("Could not parse %r for index: %s", filename, exc)

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
        try:
            tree, _language = self._differ.parse(
                content,
                filename,
                language_hint=language,
            )
            return tree
        except PluginNotFoundError:
            return None
        except Exception as exc:
            logger.debug("parse_to_tree failed for %r: %s", filename, exc)
            return None
