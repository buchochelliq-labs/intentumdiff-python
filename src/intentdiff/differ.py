"""
intentdiff.differ
~~~~~~~~~~~~~~~~~~~~~~~~

Pipeline orchestrator — the central coordinator.

Pipeline stages
───────────────
1.  Source retrieval      — call ``Source.get_content()``
2.  Language detection    — ``PluginRegistry.detect_parser(...)``
3.  Parser dispatch       - full-parse Wasm parser consumes raw source
4.  Parser output         - parser returns semantic tree JSON
5.  Trivia stripping      — ``_strip_trivia_impl`` removes comment / whitespace nodes
6.  Style-only shortcut   — compare root structural hashes; short-circuit if equal
7.  Plugin dispatch       - call ``ParserAdapter.process(source, ...)`` (Wasm)
8.  ID validation         — ``_validate_tree_ids`` catches duplicate IDs from plugins
9.  Rust finalize routing — ``finalize_review_json`` (matching, edit script,
    refinement, presentation, and grouping all run in the Rust core; the
    transitional python stages 9-13 were retired — issue #57 payoff)
13.5 Diff-analyzer pass  — ``DiffAnalyzerAdapter.analyze_diff(...)`` (Wasm, optional)

The ``SemanticDiffer`` class is the primary public API object.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from intentdiff._differ_gate import (
    RUST_CERTIFIED_LANGUAGES as RUST_CERTIFIED_LANGUAGES,
)
from intentdiff._differ_gate import (
    RUST_FINALIZE_LANGUAGES as RUST_FINALIZE_LANGUAGES,
)
from intentdiff._differ_gate import (
    STRICT_RUST_GATE_ENV as STRICT_RUST_GATE_ENV,
)
from intentdiff._differ_gate import (
    RustOnlyGateError as RustOnlyGateError,
)
from intentdiff._differ_gate import (
    _raise_rust_only_gate_error as _raise_rust_only_gate_error,
)
from intentdiff._differ_gate import (
    _rust_certified_languages as _rust_certified_languages,
)
from intentdiff._differ_gate import (
    _rust_finalize_languages as _rust_finalize_languages,
)
from intentdiff._differ_gate import (
    _strict_rust_only_enabled as _strict_rust_only_enabled,
)
from intentdiff._differ_gate import (
    _validate_git_ref as _validate_git_ref,
)
from intentdiff._differ_presentation import (
    _GENERIC_STRING_LABELS as _GENERIC_STRING_LABELS,
)
from intentdiff._differ_presentation import (
    _NAMED_ENTITY_NODE_TYPES as _NAMED_ENTITY_NODE_TYPES,
)
from intentdiff._differ_presentation import (
    _all_semantic_nodes as _all_semantic_nodes,
)
from intentdiff._differ_presentation import (
    _annotate_text_diffs as _annotate_text_diffs,
)
from intentdiff._differ_presentation import (
    _changes_to_stream_events as _changes_to_stream_events,
)
from intentdiff._differ_presentation import (
    _clean_string_literal_label as _clean_string_literal_label,
)
from intentdiff._differ_presentation import (
    _compute_structural_hash_for_tree as _compute_structural_hash_for_tree,
)
from intentdiff._differ_presentation import (
    _count_cst_nodes as _count_cst_nodes,
)
from intentdiff._differ_presentation import (
    _count_semantic_nodes as _count_semantic_nodes,
)
from intentdiff._differ_presentation import (
    _empty_semantic_tree as _empty_semantic_tree,
)
from intentdiff._differ_presentation import (
    _enrich_literal_labels as _enrich_literal_labels,
)
from intentdiff._differ_presentation import (
    _has_error_node as _has_error_node,
)
from intentdiff._differ_presentation import (
    _is_markdown_filename as _is_markdown_filename,
)
from intentdiff._differ_presentation import (
    _is_named_entity_node as _is_named_entity_node,
)
from intentdiff._differ_presentation import (
    _markdown_section_body_hashes as _markdown_section_body_hashes,
)
from intentdiff._differ_presentation import (
    _markdown_section_heading_rename_presentation as _markdown_section_heading_rename_presentation,
)
from intentdiff._differ_presentation import (
    _markdown_section_move_presentation as _markdown_section_move_presentation,
)
from intentdiff._differ_presentation import (
    _markdown_sections as _markdown_sections,
)
from intentdiff._differ_presentation import (
    _node_to_dict as _node_to_dict,
)
from intentdiff._differ_presentation import (
    _root_structural_hash as _root_structural_hash,
)
from intentdiff._differ_presentation import (
    _semantic_parent_map as _semantic_parent_map,
)
from intentdiff._differ_presentation import (
    _slice_source_text as _slice_source_text,
)
from intentdiff._differ_presentation import (
    _surface_changed_in_place_entities as _surface_changed_in_place_entities,
)
from intentdiff._differ_presentation import (
    _token_fallback_diff as _token_fallback_diff,
)
from intentdiff._differ_presentation import (
    _validate_tree_ids as _validate_tree_ids,
)
from intentdiff._differ_runtime import (
    _ADDED_FILE_STATUSES as _ADDED_FILE_STATUSES,
)
from intentdiff._differ_runtime import (
    _DEFAULT_PLUGIN_FUEL as _DEFAULT_PLUGIN_FUEL,
)
from intentdiff._differ_runtime import (
    _DELETED_FILE_STATUSES as _DELETED_FILE_STATUSES,
)
from intentdiff._differ_runtime import (
    _EXPLICIT_FUEL_EXHAUSTION_TEST_CAP as _EXPLICIT_FUEL_EXHAUSTION_TEST_CAP,
)
from intentdiff._differ_runtime import (
    _FUEL_HOTSPOT_ABSOLUTE as _FUEL_HOTSPOT_ABSOLUTE,
)
from intentdiff._differ_runtime import (
    _FUEL_HOTSPOT_PER_KB as _FUEL_HOTSPOT_PER_KB,
)
from intentdiff._differ_runtime import (
    _FUEL_HOTSPOT_PER_LINE as _FUEL_HOTSPOT_PER_LINE,
)
from intentdiff._differ_runtime import (
    _apply_file_lifecycle_to_diff as _apply_file_lifecycle_to_diff,
)
from intentdiff._differ_runtime import (
    _attach_content_type_metadata as _attach_content_type_metadata,
)
from intentdiff._differ_runtime import (
    _attach_run_telemetry as _attach_run_telemetry,
)
from intentdiff._differ_runtime import (
    _drain_plugin_telemetry as _drain_plugin_telemetry,
)
from intentdiff._differ_runtime import (
    _fuel_budget as _fuel_budget,
)
from intentdiff._differ_runtime import (
    _fuel_hotspot_for_call as _fuel_hotspot_for_call,
)
from intentdiff._differ_runtime import (
    _infer_file_lifecycle as _infer_file_lifecycle,
)
from intentdiff._differ_runtime import (
    _PhaseProfiler as _PhaseProfiler,
)
from intentdiff._differ_runtime import (
    _record_engine_telemetry as _record_engine_telemetry,
)
from intentdiff._differ_runtime import (
    _summarize_engine_telemetry as _summarize_engine_telemetry,
)
from intentdiff.analysis.compile_commands import compile_commands_metadata
from intentdiff.analysis.diagnostics import (
    DiagnosticsRecorder,
)
from intentdiff.analysis.guardrails import (
    apply_guardrails_to_diff,
    guardrails_may_apply,
)

# All five profile-enrichment families (keyed / path / query / statement /
# resource) are now Rust-authoritative — no Python enricher on the diff path (#90).
from intentdiff.analysis.schema_resolver import (
    resolve_schema,
    schema_cache_fingerprint,
    schema_resolution_metadata,
)
from intentdiff.analysis.text_review import (
    PresentationResult,
    normalize_generic_text_for_review,
)
from intentdiff.analysis.user_schemas import (
    load_user_schema_profiles,
    user_xml_dialects_payload,
)
from intentdiff.cache.store import CacheStore
from intentdiff.core.models import (
    Change,
    ChangeGroupKind,
    ChangeStreamEvent,
    ChangeStreamPhase,
    ChangeType,
    DetectionResult,
    DiffConfig,
    LanguageInfoGroup,
    SemanticDiff,
    SemanticNode,
)
from intentdiff.plugins.exceptions import (
    PluginFuelExhausted,
    PluginNotFoundError,
    PluginOutputError,
)
from intentdiff.plugins.loader import _strip_trivia_impl
from intentdiff.plugins.registry import PluginRegistry
from intentdiff.rust_core import (
    RustCoreCommitJsonAttempt,
    apply_invariances,
    build_style_only_evidence,
    enrich_node_facts,
    try_register_user_xml_dialects,
    try_rust_core_batch_diff,
    try_rust_core_batch_diffs,
    try_rust_core_commit_json,
    try_rust_core_working_tree_commit_json,
    try_rust_profile_label_enrichment,
)
from intentdiff.sources.base import Source

logger = logging.getLogger(__name__)


class SemanticDiffer:
    """
    High-level semantic differ.

    Usage::

        differ = SemanticDiffer()
        diff = differ.diff(GitSource(repo_path, "src/main.py", "HEAD~1", "HEAD"))
        print(diff.has_semantic_changes)
    """

    def __init__(
        self,
        config: DiffConfig | None = None,
        registry: PluginRegistry | None = None,
        cache: CacheStore | None = None,
    ) -> None:
        # Defensive copy so callers cannot mutate config after construction.
        self._config = config.model_copy() if config is not None else DiffConfig()
        self._registry = registry or PluginRegistry(self._config)
        self._parallel_executor: ThreadPoolExecutor | None = None
        self._parallel_executor_workers: int | None = None
        self._parallel_executor_lock = threading.Lock()
        self._parallel_worker_local = threading.local()
        # Per-grammar wasm binary hash, computed once and memoised.
        self._wasm_hash_cache: dict[str, str] = {}

        # ── Cache / analytics setup ───────────────────────────────────────
        if cache is not None:
            # Explicitly injected store (e.g. for tests)
            self._cache: CacheStore | None = cache
            self._analytics = None
        elif self._config.cache_path is not None:
            from intentdiff.cache.sqlite_store import SqliteCacheStore  # noqa: PLC0415

            self._cache = SqliteCacheStore(
                self._config.cache_path / "cache.db",
                ttl_days=self._config.cache_ttl_days,
                max_mb=self._config.cache_max_mb,
            )
            if self._config.analytics_path is not None:
                from intentdiff.cache.duckdb_store import (
                    DuckDBAnalyticsStore,  # noqa: PLC0415
                )

                self._analytics = DuckDBAnalyticsStore(
                    self._config.analytics_path / "analytics.duckdb"
                )
            else:
                self._analytics = None
        else:
            self._cache = None
            self._analytics = None

    # ── Public API ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Release background resources owned by this differ."""
        with self._parallel_executor_lock:
            executor = self._parallel_executor
            self._parallel_executor = None
            self._parallel_executor_workers = None
            self._parallel_worker_local = threading.local()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def __del__(self) -> None:
        executor = getattr(self, "_parallel_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _new_profiler(self) -> _PhaseProfiler:
        return _PhaseProfiler(bool(self._config.profile_phases))

    def _parallel_executor_for(
        self,
        max_workers: int,
    ) -> tuple[ThreadPoolExecutor, threading.local]:
        with self._parallel_executor_lock:
            if self._parallel_executor is None or self._parallel_executor_workers != max_workers:
                old_executor = self._parallel_executor
                self._parallel_worker_local = threading.local()
                self._parallel_executor = ThreadPoolExecutor(max_workers=max_workers)
                self._parallel_executor_workers = max_workers
            else:
                old_executor = None
            executor = self._parallel_executor
            worker_local = self._parallel_worker_local
        if old_executor is not None:
            old_executor.shutdown(wait=True, cancel_futures=False)
        return executor, worker_local

    def _attach_phase_timings(
        self,
        diff: SemanticDiff,
        profiler: _PhaseProfiler,
    ) -> SemanticDiff:
        if not profiler.enabled:
            return diff
        metadata = dict(diff.metadata)
        metadata["phase_timings"] = profiler.snapshot()
        return diff.model_copy(update={"metadata": metadata})

    def _attach_commit_batch_phase_timings(
        self,
        diff: SemanticDiff,
        *,
        source_collection_ms: float,
        rust_batch_ms: float,
        rust_commit_merge_ms: float = 0.0,
        rust_adapter_phase_timings: list[dict[str, Any]] | None = None,
        rust_batch_parallel_auto: bool,
        rust_batch_parallel_workers: int | None,
        rust_batch_size: int,
        rust_batch_fallback_count: int,
    ) -> SemanticDiff:
        metadata = dict(diff.metadata)
        batch_metadata = dict(metadata.get("rust_core_batch") or {})
        batch_metadata.update(
            {
                "rust_core_batch_parallel_auto": rust_batch_parallel_auto,
                "parallel_workers": rust_batch_parallel_workers,
                "batch_size": rust_batch_size,
                "fallback_count": rust_batch_fallback_count,
            }
        )
        adapter_phases = [
            {
                **phase,
                "shared": True,
            }
            for phase in rust_adapter_phase_timings or []
            if isinstance(phase, dict)
        ]
        if adapter_phases:
            batch_metadata["adapter_phase_timings"] = adapter_phases
        metadata["rust_core_batch"] = batch_metadata
        if not self._config.profile_phases:
            return diff.model_copy(update={"metadata": metadata})
        phases: list[dict[str, Any]] = [
            {
                "name": "source_collection",
                "duration_ms": round(source_collection_ms, 3),
                "shared": True,
            }
        ]
        if adapter_phases:
            phases.extend(adapter_phases)
        else:
            phases.append(
                {
                    "name": "rust_core_batch_execution",
                    "duration_ms": round(rust_batch_ms, 3),
                    "shared": True,
                }
            )
        metadata["phase_timings"] = {
            "schema_version": 1,
            "total_ms": round(
                source_collection_ms + rust_batch_ms + rust_commit_merge_ms,
                3,
            ),
            "phases": phases,
        }
        if rust_commit_merge_ms:
            metadata["phase_timings"]["phases"].append(
                {
                    "name": "commit_result_merge",
                    "duration_ms": round(rust_commit_merge_ms, 3),
                    "shared": True,
                }
            )
        return diff.model_copy(update={"metadata": metadata})

    def _rust_core_python_batch_skip_reason(self) -> str | None:
        if not self._config.experimental_rust_core:
            return "disabled"
        if self._config.diagnostics:
            return "diagnostics require Python pipeline"
        if self._config.extra_trivia_types:
            return "extra trivia types require Python pipeline"
        if self._config.extra_grammars.get("python"):
            return "custom Python grammar requires Python pipeline"
        allowed = self._config.allowed_plugins
        if allowed is not None and not any(
            plugin_id in allowed
            for plugin_id in (
                "python",
                "python-parser",
                "python_parser",
                "intentdiff:python:python",
            )
        ):
            return "Python parser is not allowed by plugin policy"
        if self._registry.get_enrichers("python"):
            return "enrichers require Python pipeline"
        if self._registry.get_diff_analyzers("python"):
            return "diff analyzers require Python pipeline"
        return None

    @staticmethod
    def _rust_core_parallel_workers(parallel: int | bool) -> int | None:
        if parallel is True:
            return os.cpu_count() or 1
        if isinstance(parallel, int) and parallel > 0:
            return parallel
        return None

    @staticmethod
    def _rust_core_batch_parallel_plan(
        file_count: int,
        parallel: int | bool,
    ) -> tuple[bool, int | None, bool]:
        """Return Rust batch parallel settings for the opt-in Rust product lane."""
        if file_count <= 1:
            return False, 1 if file_count == 1 else None, False
        cpu_count = os.cpu_count() or 1
        if parallel is True:
            return True, min(cpu_count, file_count), False
        if isinstance(parallel, int) and not isinstance(parallel, bool) and parallel > 0:
            return True, min(parallel, file_count), False
        return True, min(cpu_count, file_count), True

    def diff(self, source: Source) -> SemanticDiff:
        """
        Run the full semantic diff pipeline on a ``Source``.

        Parameters
        ----------
        source:
            Any ``Source`` subclass (``GitSource``, ``FileSource``,
            ``StringSource``, ``PatchSource``).

        Returns
        -------
        SemanticDiff
            Immutable result object.
        """
        profiler = self._new_profiler()
        with profiler.phase("source_loading"):
            old_content, new_content, filename, language_hint = source.get_content()
        return self._run_pipeline(
            old_content,
            new_content,
            filename,
            language_hint,
            _profiler=profiler,
        )

    def diff_strings(
        self,
        old: str,
        new: str,
        filename: str,
        language_hint: str | None = None,
        parser_plugin_id: str | None = None,
    ) -> SemanticDiff:
        """Convenience method for in-memory diffs."""
        return self._run_pipeline(
            old,
            new,
            filename,
            language_hint,
            parser_plugin_id=parser_plugin_id,
        )

    def detect_all(
        self,
        content: str,
        candidates: list[str] | None = None,
        preferred_plugins: dict[str, str] | None = None,
        plugin_id: str | None = None,
    ) -> list[DetectionResult]:
        """Return every parser that claims to handle *content*, ranked by priority.

        Each registered parser is asked via its own Wasm ``detect-language``
        function — no regex heuristics in Python.

        Parameters
        ----------
        content:
            Source snippet to identify (first 4 KB are inspected).
        candidates:
            Optional shortlist of language IDs.  When given, only parsers that
            handle at least one of those IDs are consulted.

        Returns
        -------
        list[DetectionResult]
            Possibly empty.  First element is the most confident match.
        """
        return self._registry.detect_by_content(
            content,
            candidates,
            preferred_plugins,
            plugin_id,
        )

    def supported_languages(self) -> list[str]:
        """Return language IDs reported by registered parser plugins.

        This is the API used by the playground language picker.  The values
        come from each parser plugin's ``language-ids`` export, so plugin
        authors own aliases and displayable IDs in one place.
        """
        return self._registry.language_ids()

    def language_info(self) -> list[LanguageInfoGroup]:
        """Return rich parser-plugin metadata grouped by language ID."""
        return self._registry.language_info()

    def detect_language(
        self,
        content: str,
        candidates: list[str] | None = None,
        preferred_plugins: dict[str, str] | None = None,
        plugin_id: str | None = None,
    ) -> DetectionResult | None:
        """Return the best-matching language for *content*, or ``None``.

        Convenience wrapper around :meth:`detect_all` that returns only the
        top result.
        """
        results = self.detect_all(content, candidates, preferred_plugins, plugin_id)
        return results[0] if results else None

    def playground_example(
        self,
        language: str,
        plugin_id: str | None = None,
    ) -> dict[str, str] | None:
        """Return the ``{\"old\": ..., \"new\": ...}`` demo pair from the plugin
        that owns *language*, or ``None`` if unavailable."""
        return self._registry.example_for(language, plugin_id)

    def parse(
        self,
        content: str,
        filename: str,
        language_hint: str | None = None,
    ) -> tuple[SemanticNode, str]:
        """
        Parse *content* as *filename* and return ``(SemanticNode, language)``.

        This runs pipeline stages 1–7 (language detection, preprocessing,
        tree-sitter parsing, trivia stripping, and Wasm plugin dispatch) and
        respects the parse-tree cache if one is configured.

        Raises :class:`~intentdiff.plugins.exceptions.PluginNotFoundError`
        when no registered parser supports *filename*.

        Parameters
        ----------
        content:
            Raw source text of the file.
        filename:
            File name used for language detection and debug messages.
        language_hint:
            Optional language override; if omitted the registry detects it.

        Returns
        -------
        tuple[SemanticNode, str]
            ``(tree, language)`` where *tree* is the fully processed semantic
            tree and *language* is the detected language identifier.
        """
        parser, language = self._registry.detect_parser(filename, content, language_hint)
        adaptive_fuel = self._config.plugin_fuel

        if parser.parser_mode == "interpret-cst":
            content = parser.preprocess_source(content)
            cst_json = self._parse(content, parser, language, filename)
            if len(cst_json) > self._config.max_cst_bytes:
                raise ValueError(
                    f"CST JSON for {filename!r} is {len(cst_json):,} characters, "
                    f"exceeding the limit of {self._config.max_cst_bytes:,} "
                    "(DiffConfig.max_cst_bytes)."
                )
            trivia_types = list(parser.trivia_node_types) + self._config.extra_trivia_types
            filtered = _strip_trivia_impl(cst_json, trivia_types)
            cst_nodes = _count_cst_nodes(filtered)
            adaptive_fuel = _fuel_budget(self._config.plugin_fuel, 20_000_000 + cst_nodes * 200_000)
            input_ = filtered
        else:
            input_ = content

        if self._cache is not None:
            node = self._cached_process(parser, input_, language, filename, adaptive_fuel)
        else:
            node = parser.process(input_, language, filename, fuel=adaptive_fuel)

        return node, language

    def render(self, diff: SemanticDiff, output_format: str = "terminal") -> str:
        """
        Render a ``SemanticDiff`` to a plain string.

        Parameters
        ----------
        diff:
            The diff result to render.
        output_format:
            Output format.  Built-in: ``"terminal"`` (default, no ANSI),
            ``"terminal-color"`` (ANSI via renderer plugin), ``"json"``.
            Plugin-provided: ``"patch"``, ``"html"``, ``"llm"``, or any
            format name registered by a third-party renderer plugin.

        Returns
        -------
        str
            The rendered string.  ``"terminal"`` output contains no ANSI codes.
        """
        if output_format == "json":
            return diff.model_dump_json(indent=2)

        if output_format == "terminal":
            # Inline Python renderer — always available, no ANSI codes.
            from io import StringIO

            from rich.console import Console as _Console

            buf = StringIO()
            console = _Console(file=buf, highlight=False, no_color=True)
            console.print(
                f"Semantic diff: {diff.old_filename} -> {diff.new_filename} ({diff.language})"
            )
            for change in diff.changes:
                ct_str = (
                    change.change_type.value
                    if isinstance(change.change_type, ChangeType)
                    else str(change.change_type)
                )
                console.print(f"  {ct_str:14} {change.description}")
            return buf.getvalue()

        try:
            renderer = self._registry.get_renderer(output_format)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        diff_json = diff.model_dump_json()
        adaptive_fuel = _fuel_budget(self._config.plugin_fuel, 20_000_000 + len(diff_json) * 100)
        return renderer.render(diff_json, fuel=adaptive_fuel)

    def _diff_commit_certified_json(
        self,
        repo_path: str | os.PathLike[str] = ".",
        old_ref: str = "HEAD",
        new_ref: str = "",
    ) -> RustCoreCommitJsonAttempt:
        """Return Rust-certified CommitDiff JSON bytes for narrow Python batches."""
        _validate_git_ref(old_ref)
        _validate_git_ref(new_ref)
        rust_batch_skip_reason = self._rust_core_python_batch_skip_reason()
        if rust_batch_skip_reason is not None:
            _raise_rust_only_gate_error(rust_batch_skip_reason)
            return RustCoreCommitJsonAttempt(fallback_reason=rust_batch_skip_reason)

        if not new_ref:
            rust_batch_parallel, rust_batch_workers, _rust_batch_parallel_auto = (
                True,
                self._rust_core_parallel_workers(self._config.parallel),
                True,
            )
            working_tree_attempt = try_rust_core_working_tree_commit_json(
                repo_path=repo_path,
                old_ref=old_ref,
                config=self._config,
                parallel=rust_batch_parallel,
                max_workers=rust_batch_workers,
            )
            if working_tree_attempt.used:
                return working_tree_attempt
            if working_tree_attempt.fallback_reason and not (
                "does not expose diff_working_tree_python_commit_json"
                in working_tree_attempt.fallback_reason
                or working_tree_attempt.fallback_reason.startswith("unavailable:")
            ):
                _raise_rust_only_gate_error(working_tree_attempt.fallback_reason)
                return working_tree_attempt

        from intentdiff.plugins.builtins import python_parser_entry
        from intentdiff.sources.git_source import (
            collect_working_tree_python_sources_fast,
            iter_changed_sources,
        )

        source_started = time.perf_counter()
        fast_collection = (
            collect_working_tree_python_sources_fast(repo_path, old_ref) if not new_ref else None
        )
        if fast_collection is None:
            sources = list(iter_changed_sources(repo_path, old_ref, new_ref))
            fast_fallback_reason = None
        else:
            sources, fast_fallback_reason = fast_collection
        source_collection_ms = (time.perf_counter() - source_started) * 1000
        if fast_fallback_reason is not None:
            _raise_rust_only_gate_error(fast_fallback_reason)
            return RustCoreCommitJsonAttempt(fallback_reason=fast_fallback_reason)
        if not sources:
            return RustCoreCommitJsonAttempt()

        parser_wasm_path = python_parser_entry()
        request_files: list[dict[str, Any]] = []
        source_filter_started = time.perf_counter()
        for old_content, new_content, old_path, new_path, staging_status in sources:
            effective_new_path = new_path if new_path is not None else old_path
            file_lifecycle = _infer_file_lifecycle(old_content, new_content, staging_status)
            old_path_lower = old_path.lower()
            effective_new_path_lower = effective_new_path.lower()
            if not (
                old_path_lower.endswith((".py", ".pyi"))
                or effective_new_path_lower.endswith((".py", ".pyi"))
            ):
                fallback_attempt = RustCoreCommitJsonAttempt(
                    fallback_reason="certified commit JSON requires all changed files to be Python"
                )
                _raise_rust_only_gate_error(fallback_attempt.fallback_reason)
                return fallback_attempt
            # Protected guardrail rules currently apply only to config/profile
            # languages, not Python.  Avoid a per-file policy search here; any
            # actual policy-file change is excluded by the Python-only gate.
            request_file: dict[str, Any] = {
                "old_source": old_content,
                "new_source": new_content,
                "old_filename": old_path,
                "new_filename": effective_new_path,
                "language": "python",
                "parser_plugin_id": "python",
                "parser_wasm_path": parser_wasm_path,
                "file_lifecycle": file_lifecycle,
            }
            if staging_status is not None:
                request_file["staging_status"] = staging_status
            request_files.append(request_file)
        source_filter_ms = (time.perf_counter() - source_filter_started) * 1000

        rust_batch_parallel, rust_batch_workers, _rust_batch_parallel_auto = (
            self._rust_core_batch_parallel_plan(len(request_files), self._config.parallel)
        )
        attempt = try_rust_core_commit_json(
            files=request_files,
            old_ref=old_ref,
            new_ref=new_ref,
            config=self._config,
            parallel=rust_batch_parallel,
            max_workers=rust_batch_workers,
        )
        if not attempt.used:
            _raise_rust_only_gate_error(attempt.fallback_reason)
            return attempt
        adapter_phase_timings = [
            {
                "name": "source_collection",
                "duration_ms": round(source_collection_ms, 3),
                "shared": True,
            },
            {
                "name": "rust_core_commit_source_filtering",
                "duration_ms": round(source_filter_ms, 3),
                "shared": True,
            },
            *attempt.adapter_phase_timings,
        ]
        control = dict(attempt.control)
        control["source_collection_ms"] = round(source_collection_ms, 3)
        control["source_filter_ms"] = round(source_filter_ms, 3)
        return RustCoreCommitJsonAttempt(
            control=control,
            commit_diff_json=attempt.commit_diff_json,
            adapter_phase_timings=adapter_phase_timings,
            backend_version=attempt.backend_version,
        )

    def diff_commit(
        self,
        repo_path: str | os.PathLike[str] = ".",
        old_ref: str = "HEAD",
        new_ref: str = "",
    ) -> list[SemanticDiff]:
        _validate_git_ref(old_ref)
        _validate_git_ref(new_ref)
        """
        Diff every file that changed between two git refs.

        Files that have no registered parser are silently skipped.

        Parameters
        ----------
        repo_path:
            Path to (any directory inside) the git repository.
        old_ref:
            Old git ref (commit SHA, tag, branch).  Defaults to ``"HEAD"``.
        new_ref:
            New git ref.  Defaults to ``""`` (working tree — diff against
            current unsaved/uncommitted files).

        Returns
        -------
        list[SemanticDiff]
            One entry per changed file that could be parsed.
        """
        from intentdiff.sources.git_source import iter_changed_sources

        source_started = time.perf_counter()
        sources = list(iter_changed_sources(repo_path, old_ref, new_ref))
        source_collection_ms = (time.perf_counter() - source_started) * 1000
        rust_batch_results: dict[int, SemanticDiff] = {}
        rust_batch_attempted: set[int] = set()
        rust_batch_skip_reason = self._rust_core_python_batch_skip_reason()
        if rust_batch_skip_reason is None and sources:
            from intentdiff.plugins.builtins import python_parser_entry

            parser_wasm_path = python_parser_entry()
            request_files: list[dict[str, Any]] = []
            request_to_source_index: list[int] = []
            rust_batch_source_filter_started = time.perf_counter()
            for idx, item in enumerate(sources):
                old_content, new_content, old_path, new_path, _staging_status = item
                effective_new_path = new_path if new_path is not None else old_path
                file_lifecycle = _infer_file_lifecycle(old_content, new_content, _staging_status)
                old_path_lower = old_path.lower()
                effective_new_path_lower = effective_new_path.lower()
                if not (
                    old_path_lower.endswith((".py", ".pyi"))
                    or effective_new_path_lower.endswith((".py", ".pyi"))
                ):
                    continue
                # Protected guardrail rules currently apply only to
                # config/profile languages.  Certified Python batches can skip
                # the expensive policy lookup; policy-file changes are not
                # Python files and therefore remain on the fallback path.
                request_to_source_index.append(idx)
                request_files.append(
                    {
                        "old_source": old_content,
                        "new_source": new_content,
                        "old_filename": old_path,
                        "new_filename": effective_new_path,
                        "language": "python",
                        "parser_plugin_id": "python",
                        "parser_wasm_path": parser_wasm_path,
                        "file_lifecycle": file_lifecycle,
                    }
                )
            rust_batch_source_filter_ms = (
                time.perf_counter() - rust_batch_source_filter_started
            ) * 1000
            if request_files:
                rust_batch_attempted = set(request_to_source_index)
                (
                    rust_batch_parallel,
                    rust_batch_workers,
                    rust_batch_parallel_auto,
                ) = self._rust_core_batch_parallel_plan(
                    len(request_files),
                    self._config.parallel,
                )
                rust_batch_started = time.perf_counter()
                rust_batch = try_rust_core_batch_diffs(
                    files=request_files,
                    config=self._config,
                    parallel=rust_batch_parallel,
                    max_workers=rust_batch_workers,
                )
                rust_batch_ms = (time.perf_counter() - rust_batch_started) * 1000
                rust_batch_fallback_count = int(
                    rust_batch.batch_metadata.get("fallback_count")
                    or len(rust_batch.fallback_reasons)
                )
                # A per-item batch fallback is NOT a Python fallback: the declined
                # files re-run through ``_run_one`` -> ``_run_pipeline`` below, whose
                # per-stage Rust finalize serves them natively. The RUST_ONLY gate
                # therefore does not fire here; it fires inside ``_run_pipeline`` only
                # if an item genuinely reaches a token-level fallback (and the fan-out
                # re-raises that RustOnlyGateError instead of swallowing it).
                rust_merge_started = time.perf_counter()
                rust_merged_results: list[tuple[int, SemanticDiff]] = []
                for request_index, diff in rust_batch.diffs.items():
                    if request_index < 0 or request_index >= len(request_to_source_index):
                        continue
                    source_index = request_to_source_index[request_index]
                    _old_content, _new_content, _old_path, _new_path, staging_status = sources[
                        source_index
                    ]
                    diff = _apply_file_lifecycle_to_diff(
                        diff,
                        _infer_file_lifecycle(_old_content, _new_content, staging_status),
                    )
                    if staging_status is not None:
                        diff = diff.model_copy(update={"staging_status": staging_status})
                    rust_merged_results.append((source_index, diff))
                rust_commit_merge_ms = (time.perf_counter() - rust_merge_started) * 1000
                for source_index, diff in rust_merged_results:
                    rust_batch_results[source_index] = self._attach_commit_batch_phase_timings(
                        diff,
                        source_collection_ms=source_collection_ms,
                        rust_batch_ms=rust_batch_ms,
                        rust_commit_merge_ms=rust_commit_merge_ms,
                        rust_adapter_phase_timings=[
                            {
                                "name": "rust_core_commit_source_filtering",
                                "duration_ms": round(rust_batch_source_filter_ms, 3),
                                "shared": True,
                            },
                            *rust_batch.adapter_phase_timings,
                        ],
                        rust_batch_parallel_auto=rust_batch_parallel_auto,
                        rust_batch_parallel_workers=rust_batch_workers,
                        rust_batch_size=len(request_files),
                        rust_batch_fallback_count=rust_batch_fallback_count,
                    )
                if rust_batch.fallback_reasons or rust_batch.fallback_reason:
                    logger.debug(
                        "Rust core commit batch used %d/%d files; fallbacks=%s reason=%s",
                        len(rust_batch_results),
                        len(request_files),
                        rust_batch.fallback_reasons,
                        rust_batch.fallback_reason,
                    )
        elif rust_batch_skip_reason is not None:
            # The commit BATCH shortcut was skipped (guardrails / enrichers / analyzers
            # / diagnostics / trivia / policy / rust-core disabled). Every source now
            # runs through ``_run_one`` -> ``_run_pipeline`` below, which is the native
            # per-stage Rust finalize path; the RUST_ONLY gate fires there, and only if
            # an item genuinely reaches a token-level fallback. A batch skip is not
            # itself a Python fallback, so no gate fires here.
            logger.debug("Rust core commit batch skipped: %s", rust_batch_skip_reason)

        def _run_one(
            differ: SemanticDiffer,
            item: tuple[str, str, str, str | None, str | None],
            *,
            include_source_collection: bool = False,
        ) -> SemanticDiff:
            old_content, new_content, old_path, new_path, staging_status = item
            profiler = differ._new_profiler()
            if include_source_collection:
                profiler.record("source_collection", source_collection_ms, shared=True)
            result = differ._run_pipeline(
                old_content,
                new_content,
                old_path,
                None,
                new_filename=new_path,
                _profiler=profiler,
            )
            if staging_status is not None:
                result = result.model_copy(update={"staging_status": staging_status})
            return result

        _skip_exc = (PluginNotFoundError,)
        _warn_exc = (PluginFuelExhausted, ValueError, RuntimeError)

        parallel = self._config.parallel
        if not parallel:
            # ── Sequential (default) ─────────────────────────────────────────
            results: list[SemanticDiff] = []
            for idx, item in enumerate(sources):
                if idx in rust_batch_results:
                    results.append(rust_batch_results[idx])
                    continue
                # Files the commit BATCH attempted but declined re-run through the normal
                # differ with the Rust core ON: the batch re-attempt is one cheap native
                # call and the per-stage finalize routing then serves. (The old
                # experimental_rust_core=False fallback differ meant "python pipeline";
                # post-retirement it means the token-fallback kill switch — a comment-only
                # commit came back as a token ADDITION instead of style-only.)
                run_differ = self
                try:
                    results.append(
                        _run_one(
                            run_differ,
                            item,
                            include_source_collection=idx == 0 and not rust_batch_results,
                        )
                    )
                except RustOnlyGateError:
                    # A genuine token-level fallback under the RUST_ONLY gate is fatal —
                    # propagate it rather than let ``_warn_exc`` silently drop the file.
                    raise
                except _skip_exc:
                    logger.debug("Skipping %r — no parser available", item[2])
                except _warn_exc:
                    logger.warning("Failed to diff %r", item[2], exc_info=True)
            return results
        else:
            # ── Parallel ────────────────────────────────────────────────
            max_workers = (os.cpu_count() or 1) if parallel is True else int(parallel)
            executor, worker_local = self._parallel_executor_for(max_workers)

            def _worker_differ() -> SemanticDiffer:
                differ = getattr(worker_local, "differ", None)
                if differ is None:
                    differ = SemanticDiffer(config=self._config.model_copy())
                    worker_local.differ = differ
                return differ


            def _run_worker(
                idx: int,
                item: tuple[str, str, str, str | None, str | None],
            ) -> SemanticDiff:
                # Batch-declined files re-run with the Rust core ON (see the
                # sequential branch note — the finalize tier serves them).
                return _run_one(
                    _worker_differ(),
                    item,
                    include_source_collection=idx == 0 and not rust_batch_results,
                )

            results_map: dict[int, SemanticDiff] = dict(rust_batch_results)
            if executor is not None:
                future_to_idx = {
                    executor.submit(_run_worker, i, item): i
                    for i, item in enumerate(sources)
                    if i not in rust_batch_results
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        results_map[idx] = future.result()
                    except RustOnlyGateError:
                        # Fatal under the RUST_ONLY gate — propagate, do not drop.
                        raise
                    except _skip_exc:
                        logger.debug("Skipping %r — no parser available", sources[idx][2])
                    except _warn_exc:
                        logger.warning("Failed to diff %r", sources[idx][2], exc_info=True)
            return [results_map[i] for i in sorted(results_map)]

    def diff_stream(
        self,
        source: Source,
    ) -> Iterator[Change]:
        """
        Yield ``Change`` objects one-by-one from a single-file diff.

        The full pipeline runs before the first item is yielded.  This is a
        convenience wrapper over :meth:`diff` for callers that prefer a
        generator interface (e.g. ``intentdiff file --stream``).
        """
        diff = self.diff(source)
        yield from diff.changes

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        old_content: str,
        new_content: str,
        filename: str,
        language_hint: str | None,
        *,
        new_filename: str | None = None,
        parser_plugin_id: str | None = None,
        _profiler: _PhaseProfiler | None = None,
    ) -> SemanticDiff:
        profiler = _profiler or self._new_profiler()
        result = self._run_stages_1_to_11(
            old_content,
            new_content,
            filename,
            language_hint,
            new_filename=new_filename,
            parser_plugin_id=parser_plugin_id,
            profiler=profiler,
        )
        # _run_stages_1_to_11 always returns a finished SemanticDiff since the python
        # pipeline retirement (issue #57 payoff, stage 4b).
        diff = result
        diff = _attach_content_type_metadata(diff, old_content, new_content)
        return self._attach_phase_timings(diff, profiler)

    def _run_diff_analyzers_on_routed(
        self,
        routed: SemanticDiff,
        language: str,
        filename: str,
        adaptive_fuel: int,
    ) -> SemanticDiff:
        for analyzer in self._registry.get_diff_analyzers(language):
            try:
                updated_json = analyzer.analyze_diff(
                    routed.model_dump_json(),
                    language,
                    filename,
                    fuel=adaptive_fuel,
                )
                routed = SemanticDiff.model_validate_json(updated_json)
            except Exception as exc:
                logger.warning(
                    "Diff analyzer %r failed for %r: %s - skipping",
                    getattr(analyzer, "provenance", "?"),
                    filename,
                    exc,
                )
                continue
        return routed

    def _run_stages_1_to_11(
        self,
        old_content: str,
        new_content: str,
        filename: str,
        language_hint: str | None,
        *,
        new_filename: str | None = None,
        parser_plugin_id: str | None = None,
        profiler: _PhaseProfiler | None = None,
    ) -> SemanticDiff:
        """Run pipeline stages 1–11 (parse → trivia-strip → Wasm → GumTree → moves).

        Returns ``SemanticDiff`` for early exits (cache hit, style-only, fallback).
        """
        # ── 1 + 2. Language detection ────────────────────────────────────────
        profiler = profiler or self._new_profiler()
        with profiler.phase("parser_selection"):
            parser, language = self._registry.detect_parser(
                filename,
                new_content,
                language_hint,
                plugin_id=parser_plugin_id,
                phase_recorder=profiler.record if profiler.enabled else None,
            )
        logger.debug("Using parser %r for %r (language=%r)", parser.grammar_id, filename, language)

        diagnostics = DiagnosticsRecorder(
            enabled=self._config.diagnostics,
            max_events=self._config.diagnostics_max_events,
        )
        engine_telemetry: list[dict[str, Any]] = []
        parse_mode = parser.parser_mode
        cache_grammar_id = parser.grammar_id
        diagnostics.summary.update(
            {
                "language": language,
                "old_filename": filename,
                "new_filename": new_filename if new_filename is not None else filename,
                "parser_grammar_id": parser.grammar_id,
                "effective_grammar_id": cache_grammar_id,
                "parse_mode": parse_mode,
            }
        )
        diagnostics.record(
            stage="parser",
            action="select_parser",
            rule_id="pipeline.parser_selection",
            reason="parser selected by registry",
            metadata={
                "language_hint": language_hint,
                "parser_plugin_id": parser_plugin_id,
                "grammar_id": parser.grammar_id,
                "effective_grammar_id": cache_grammar_id,
                "parse_mode": parse_mode,
            },
        )

        # Adaptive fuel starts at the configured floor; the interpret-cst branch
        # raises it based on CST node count before calling the plugin.
        adaptive_fuel: int = self._config.plugin_fuel

        # Save originals for the enricher pass (enrichers see un-preprocessed source)
        old_raw, new_raw = old_content, new_content
        file_lifecycle = _infer_file_lifecycle(old_raw, new_raw)
        diagnostics.summary["file_lifecycle"] = file_lifecycle
        with profiler.phase("schema_resolution"):
            schema_resolution = resolve_schema(
                content=new_content or old_content,
                filename=new_filename if new_filename is not None else filename,
                language=language,
            )
        if language.lower() == "xml":
            # User XML dialects (issue #86): marshal the descriptor registry's
            # declarative coordinate specs into the Rust matcher. Idempotent per
            # payload; dialects are match-predicated engine-side, so registering
            # the full set is safe for every xml diff.
            _user_profiles, _ = load_user_schema_profiles()
            _dialects = user_xml_dialects_payload(_user_profiles)
            if _dialects:
                try_register_user_xml_dialects(_dialects)
        schema_fingerprint = schema_cache_fingerprint(schema_resolution)
        cache_schema_suffix = (
            "" if schema_fingerprint == "schema:none" else f"|{schema_fingerprint}"
        )
        schema_metadata = schema_resolution_metadata(schema_resolution)
        if schema_metadata is not None:
            diagnostics.summary["schema"] = schema_metadata
            diagnostics.record(
                stage="schema",
                action="resolve_schema",
                rule_id="analysis.schema_resolution",
                reason="JSON/YAML schema source resolved for optional keyed-data hints",
                metadata=schema_metadata,
            )
        with profiler.phase("compile_command_resolution"):
            compile_metadata = compile_commands_metadata(
                filename=new_filename if new_filename is not None else filename,
                language=language,
            )
        if compile_metadata is not None:
            diagnostics.summary["compile_commands"] = compile_metadata
            diagnostics.record(
                stage="compile_commands",
                action="resolve_compile_command",
                rule_id="analysis.compile_commands",
                reason="C/C++ compile database context resolved",
                metadata=compile_metadata,
            )
        compile_cache_suffix = (
            "" if compile_metadata is None else f"|compile:{compile_metadata['fingerprint']}"
        )

        # Cache state — resolved later once we have preprocessed content.
        _diff_key: str | None = None
        _guardrail_cache_bypass = guardrails_may_apply(filename, new_filename, self._config)
        cache_grammar_id = parser.grammar_id

        if (
            self._config.experimental_rust_core
            and language in _rust_certified_languages()
            and not diagnostics.enabled
            # ``test_matching_engine`` is the dual-run truthiness override.
            # When set, it forces the matching stage to either Rust or
            # Python; the certified Python batch path bypasses that stage
            # entirely, so we must skip it to make the override meaningful.
            and self._config.test_matching_engine is None
        ):
            rust_core_skip_reason: str | None = None
            if _guardrail_cache_bypass:
                rust_core_skip_reason = "guardrails may affect final diff"
            elif self._config.extra_trivia_types:
                rust_core_skip_reason = "extra trivia types require Python pipeline"
            else:
                with profiler.phase("rust_core_certified_surface_check"):
                    if self._registry.get_enrichers(language):
                        rust_core_skip_reason = "enrichers require Python pipeline"
                    elif self._registry.get_diff_analyzers(language):
                        rust_core_skip_reason = "diff analyzers require Python pipeline"

            if rust_core_skip_reason is None:
                with profiler.phase("rust_core_batch_execution"):
                    rust_batch_attempt = try_rust_core_batch_diff(
                        old_source=old_content,
                        new_source=new_content,
                        old_filename=filename,
                        new_filename=new_filename if new_filename is not None else filename,
                        parser_wasm_path=parser.wasm_path,
                        language=language,
                        parser_plugin_id=parser.plugin_id or parser.grammar_id,
                        config=self._config,
                        file_lifecycle=file_lifecycle,
                    )
                if rust_batch_attempt.used and rust_batch_attempt.diff is not None:
                    diagnostics.record(
                        stage="rust_core",
                        action="batch_final_diff",
                        rule_id="rust_core.batch_v4",
                        reason="Rust core produced a certified public diff",
                        metadata={
                            "engine": rust_batch_attempt.diff.metadata.get("rust_core", {}).get(
                                "engine"
                            ),
                            "stage": rust_batch_attempt.diff.metadata.get("rust_core", {}).get(
                                "stage"
                            ),
                        },
                    )
                    return _apply_file_lifecycle_to_diff(
                        rust_batch_attempt.diff,
                        file_lifecycle,
                    )
                # The certified BATCH shortcut declined — fall through to the native
                # routed finalize path below. This is a Rust→Rust transition (the
                # routed path finalizes through the Rust core for every certified
                # language), NOT a Python fallback, so the RUST_ONLY gate does NOT
                # fire here. It fires only at the genuine token-level fallback sites
                # (parse errors / finalize declined / rust core disabled), where the
                # coarse Python token diff — the last non-Rust producer — takes over.
                logger.debug(
                    "Rust core batch declined for %r: %s; using routed finalize",
                    filename,
                    rust_batch_attempt.fallback_reason or rust_core_skip_reason,
                )
            else:
                logger.debug(
                    "Rust core batch path skipped for %r: %s; using routed finalize",
                    filename,
                    rust_core_skip_reason,
                )

        if parse_mode == "interpret-cst":
            trivia_types = list(parser.trivia_node_types) + self._config.extra_trivia_types

            # ── 2.5. Source preprocessing ────────────────────────────────────
            with profiler.phase("source_preprocessing"):
                old_content = parser.preprocess_source(old_content)
                new_content = parser.preprocess_source(new_content)

            # ── 2.6. Diff-level cache check ───────────────────────────────────
            if self._cache is not None and not diagnostics.enabled and not _guardrail_cache_bypass:
                _wasm_hash = self._wasm_hash_for(parser)
                _diff_key = self._cache.diff_key(
                    old_content,
                    new_content,
                    f"{cache_grammar_id}{cache_schema_suffix}{compile_cache_suffix}",
                    _wasm_hash,
                )
                _cached = self._cache.get_diff(_diff_key)
                if _cached is not None:
                    logger.debug("Diff cache hit for %r", filename)
                    return _apply_file_lifecycle_to_diff(
                        SemanticDiff.model_validate_json(_cached),
                        file_lifecycle,
                    )

            old_cst_json = self._parse(
                old_content,
                parser,
                language,
                filename,
                profiler=profiler,
            )
            new_cst_json = self._parse(
                new_content,
                parser,
                language,
                filename,
                profiler=profiler,
            )

            # ── CST size guard ────────────────────────────────────────────────
            for _label, _cst in (("old", old_cst_json), ("new", new_cst_json)):
                if len(_cst) > self._config.max_cst_bytes:
                    raise ValueError(
                        f"CST JSON for the {_label!r} version of {filename!r} is "
                        f"{len(_cst):,} characters, exceeding the limit of "
                        f"{self._config.max_cst_bytes:,} "
                        "(DiffConfig.max_cst_bytes). Increase the limit or switch "
                        "to a full-parse plugin."
                    )

            # ── 5. Trivia stripping ───────────────────────────────────────────
            with profiler.phase("trivia_stripping"):
                old_filtered = _strip_trivia_impl(old_cst_json, trivia_types)
                new_filtered = _strip_trivia_impl(new_cst_json, trivia_types)

            # ── 6. Style-only shortcut ────────────────────────────────────────
            with profiler.phase("style_hashing"):
                old_hash = _compute_structural_hash_for_tree(old_filtered)
                new_hash = _compute_structural_hash_for_tree(new_filtered)

            if old_hash == new_hash:
                logger.debug("Style-only diff for %r", filename)
                diagnostics.record(
                    stage="style",
                    action="style_only_shortcut",
                    rule_id="pipeline.style_only_shortcut",
                    reason="filtered CST structural hashes are equal",
                    metadata={"old_hash": old_hash, "new_hash": new_hash},
                )
                style_evidence = build_style_only_evidence(
                    old_source=old_content,
                    new_source=new_content,
                    language=language,
                    old_cst_json=old_cst_json,
                    new_cst_json=new_cst_json,
                )
                metadata: dict[str, Any] = {}
                if style_evidence.ignored_style_changes:
                    metadata["ignored_style_changes"] = style_evidence.ignored_style_changes
                for group in style_evidence.change_groups:
                    diagnostics.record_group(stage="invariance", group=group)
                if diagnostics.enabled:
                    diagnostics.summary.update(
                        {
                            "early_exit": "style_only",
                            "final_change_count": 0,
                            "final_group_count": len(style_evidence.change_groups),
                        }
                    )
                    metadata["diagnostics"] = diagnostics.snapshot()
                style_diff = SemanticDiff.style_only(
                    filename,
                    new_filename if new_filename is not None else filename,
                    language,
                    change_groups=style_evidence.change_groups,
                    metadata=metadata,
                )
                style_diff = _apply_file_lifecycle_to_diff(style_diff, file_lifecycle)
                return apply_guardrails_to_diff(
                    style_diff,
                    old_tree=None,
                    new_tree=None,
                    old_source=old_raw,
                    new_source=new_raw,
                    config=self._config,
                    diagnostics=diagnostics,
                )

            # ── 6.5. Adaptive fuel budget ─────────────────────────────────────
            old_cst_nodes = _count_cst_nodes(old_filtered)
            new_cst_nodes = _count_cst_nodes(new_filtered)
            max_cst_nodes = max(old_cst_nodes, new_cst_nodes)
            adaptive_fuel = _fuel_budget(
                self._config.plugin_fuel,
                20_000_000 + max_cst_nodes * 200_000,
            )
            logger.debug(
                "[%s] CST: old=%d bytes/%d nodes, new=%d bytes/%d nodes — fuel=%d",
                filename,
                len(old_filtered),
                old_cst_nodes,
                len(new_filtered),
                new_cst_nodes,
                adaptive_fuel,
            )
            diagnostics.record(
                stage="parse",
                action="interpret_cst",
                rule_id="pipeline.interpret_cst_parse",
                reason="host CST parsed, stripped, and sized",
                metadata={
                    "old_cst_bytes": len(old_filtered),
                    "new_cst_bytes": len(new_filtered),
                    "old_cst_nodes": old_cst_nodes,
                    "new_cst_nodes": new_cst_nodes,
                    "adaptive_fuel": adaptive_fuel,
                },
            )

            # ── 7. Plugin dispatch (with parse-tree cache) ───────────────────
            try:
                with profiler.phase("wasm_plugin_execution"):
                    if self._cache is not None:
                        old_tree = self._cached_process(
                            parser,
                            old_filtered,
                            language,
                            filename,
                            adaptive_fuel,
                            cache_grammar_id=cache_grammar_id,
                        )
                        new_tree = self._cached_process(
                            parser,
                            new_filtered,
                            language,
                            filename,
                            adaptive_fuel,
                            cache_grammar_id=cache_grammar_id,
                        )
                    else:
                        old_tree = parser.process(
                            old_filtered, language, filename, fuel=adaptive_fuel
                        )
                        new_tree = parser.process(
                            new_filtered, language, filename, fuel=adaptive_fuel
                        )
            except PluginFuelExhausted as exc:
                raise PluginFuelExhausted(
                    exc.plugin_id,
                    exc.fuel,
                    f"file={filename!r}, CST nodes up to {max_cst_nodes:,}",
                ) from exc

        else:
            # ── 7 (full-parse). Plugin owns parsing — send raw source ─────────
            # Stages 3-6 are skipped: the plugin handles its own CST construction
            # and trivia filtering. The style-only shortcut is not available here
            # because there is no host-side CST to hash.
            max_source_bytes = max(
                len(old_content.encode("utf-8")),
                len(new_content.encode("utf-8")),
            )
            adaptive_fuel = _fuel_budget(
                self._config.plugin_fuel,
                50_000_000 + max_source_bytes * 150_000,
            )
            logger.debug("full-parse mode for %r via %r", filename, parser.grammar_id)

            # Diff-level cache check for full-parse mode.
            if self._cache is not None and not diagnostics.enabled and not _guardrail_cache_bypass:
                _wasm_hash = self._wasm_hash_for(parser)
                _diff_key = self._cache.diff_key(
                    old_content,
                    new_content,
                    f"{parser.grammar_id}{cache_schema_suffix}{compile_cache_suffix}",
                    _wasm_hash,
                )
                _cached = self._cache.get_diff(_diff_key)
                if _cached is not None:
                    logger.debug("Diff cache hit for %r (full-parse)", filename)
                    return _apply_file_lifecycle_to_diff(
                        SemanticDiff.model_validate_json(_cached),
                        file_lifecycle,
                    )

            with profiler.phase("wasm_plugin_execution"):
                old_side_absent = file_lifecycle == "added" and old_content == ""
                new_side_absent = file_lifecycle == "deleted" and new_content == ""
                if self._cache is not None:
                    old_tree = (
                        _empty_semantic_tree(language)
                        if old_side_absent
                        else self._cached_process(
                            parser, old_content, language, filename, adaptive_fuel
                        )
                    )
                    new_tree = (
                        _empty_semantic_tree(language)
                        if new_side_absent
                        else self._cached_process(
                            parser, new_content, language, filename, adaptive_fuel
                        )
                    )
                else:
                    old_tree = (
                        _empty_semantic_tree(language)
                        if old_side_absent
                        else parser.process(old_content, language, filename, fuel=adaptive_fuel)
                    )
                    new_tree = (
                        _empty_semantic_tree(language)
                        if new_side_absent
                        else parser.process(new_content, language, filename, fuel=adaptive_fuel)
                    )
            diagnostics.record(
                stage="parse",
                action="full_parse",
                rule_id="pipeline.full_parse",
                reason="plugin parsed raw source",
                metadata={"adaptive_fuel": adaptive_fuel},
            )

        engine_telemetry.extend(_drain_plugin_telemetry(parser))
        _record_engine_telemetry(diagnostics, engine_telemetry)

        # ── 7b. Enrichment pass (optional) ─────────────────────────────────────────
        with profiler.phase("enricher_execution"):
            enrichers = self._registry.get_enrichers(language)
            if enrichers:
                old_tree_json = old_tree.model_dump_json()
                new_tree_json = new_tree.model_dump_json()
                for enricher in enrichers:
                    old_tree_json = enricher.enrich(
                        old_tree_json, old_raw, language, filename, fuel=adaptive_fuel
                    )
                    new_tree_json = enricher.enrich(
                        new_tree_json, new_raw, language, filename, fuel=adaptive_fuel
                    )
                    engine_telemetry.extend(_drain_plugin_telemetry(enricher))
                    _record_engine_telemetry(diagnostics, engine_telemetry)
                try:
                    old_tree = SemanticNode.model_validate_json(old_tree_json)
                    new_tree = SemanticNode.model_validate_json(new_tree_json)
                except ValidationError as exc:
                    raise PluginOutputError("enricher", str(exc)) from exc
        # ── 8. Plugin output validation ────────────────────────────────────────
        with profiler.phase("semantic_node_validation"):
            old_tree = _enrich_literal_labels(old_tree, old_raw)
            new_tree = _enrich_literal_labels(new_tree, new_raw)
            # Cross-language NodeFacts (issue #70): every tree (all parse branches converge here)
            # gets language-agnostic structural facts derived in the Rust core, so the intent
            # explainer has signal for ALL languages, not just Python. Idempotent + graceful.
            old_tree = enrich_node_facts(old_tree)
            new_tree = enrich_node_facts(new_tree)
            # Profile-label enrichment runs in the Rust core (issue #57 port).
            # The Rust core is AUTHORITATIVE (readiness #90 / #82): no Python
            # engine fallback — if the backend is unavailable the tree passes
            # through UNCHANGED (graceful, degraded labels) rather than routing
            # to a Python enricher, so "if Python didn't exist it still works".
            # Gated by language so non-profile diffs pay no tree-JSON roundtrip.
            if language.lower() in {"json", "yaml"}:
                _identity_fields = tuple(schema_resolution.identity_fields)
                old_tree = try_rust_profile_label_enrichment(
                    old_tree, old_raw, language, identity_fields=_identity_fields
                ) or old_tree
                new_tree = try_rust_profile_label_enrichment(
                    new_tree, new_raw, language, identity_fields=_identity_fields
                ) or new_tree
            if language.lower() in {"css", "scss", "html", "xml", "mdx"}:
                old_tree = try_rust_profile_label_enrichment(old_tree, old_raw, language) or old_tree
                new_tree = try_rust_profile_label_enrichment(new_tree, new_raw, language) or new_tree
            if language.lower() in {"hcl", "puppet"}:
                old_tree = try_rust_profile_label_enrichment(old_tree, old_raw, language) or old_tree
                new_tree = try_rust_profile_label_enrichment(new_tree, new_raw, language) or new_tree
            if language.lower() == "sql":
                old_tree = try_rust_profile_label_enrichment(old_tree, old_raw, language) or old_tree
                new_tree = try_rust_profile_label_enrichment(new_tree, new_raw, language) or new_tree
            if language.lower() in {"asm", "bash", "delphi"}:
                old_tree = try_rust_profile_label_enrichment(old_tree, old_raw, language) or old_tree
                new_tree = try_rust_profile_label_enrichment(new_tree, new_raw, language) or new_tree
            _validate_tree_ids(old_tree, filename)
            _validate_tree_ids(new_tree, filename)
        diagnostics.record(
            stage="parse",
            action="semantic_tree_ready",
            rule_id="pipeline.semantic_tree_ready",
            reason="semantic trees produced and enriched",
            metadata={
                "old_nodes": _count_semantic_nodes(old_tree),
                "new_nodes": _count_semantic_nodes(new_tree),
            },
        )
        # ── 8.5. Token-level fallback for severe parse errors ───────────────
        # When tree-sitter produced ERROR nodes and fallback is enabled, skip
        # the GumTree algorithm and return a coarse token-level diff instead.
        if self._config.fallback_to_token_diff and (
            _has_error_node(old_tree) or _has_error_node(new_tree)
        ):
            logger.warning(
                "Parse errors detected in %r — falling back to token-level diff",
                filename,
            )
            _raise_rust_only_gate_error("parse errors require Rust token-level fallback")
            diagnostics.record(
                stage="fallback",
                action="token_fallback",
                rule_id="pipeline.token_fallback",
                reason="semantic tree contains ERROR nodes",
            )
            metadata: dict[str, Any] = {}
            if diagnostics.enabled:
                diagnostics.summary.update(
                    {"early_exit": "token_fallback", "final_change_count": None}
                )
                metadata["diagnostics"] = diagnostics.snapshot()
            fallback_diff = _token_fallback_diff(
                old_raw,
                new_raw,
                filename,
                new_filename if new_filename is not None else filename,
                language,
                metadata=metadata,
            )
            fallback_diff = _apply_file_lifecycle_to_diff(fallback_diff, file_lifecycle)
            return apply_guardrails_to_diff(
                fallback_diff,
                old_tree=None,
                new_tree=None,
                old_source=old_raw,
                new_source=new_raw,
                config=self._config,
                diagnostics=diagnostics,
            )
        # ── 9. GumTree matching ────────────────────────────────────────────────
        # The matching engine is selected by (in priority order):
        # 1. ``DiffConfig.test_matching_engine`` — test-only hard override used
        #    by the dual-run truthiness matrix to certify both contracts.
        # 2. The production default: Rust's language-agnostic matcher
        #    (``intentdiff_rust_core.diff_semantic_tree_json``) when the Rust
        #    core is available and diagnostics are off. This is migration
        #    step 2 of ``docs/ENGINE_BOUNDARY_AUDIT.md``. The four engine
        #    gaps that previously blocked this swap (Python dedent, JS
        #    calc_hash MOVE, YAML block↔flow, C# var↔explicit) are closed —
        #    see ``the retired NOISE_SUPPRESSION_RETUNE doc (git history)`` Phases A–D.
        # 3. (retired) The python GumTree oracle was deleted with the transitional
        #    layer (issue #57 payoff, stage 4b) — no python matching fallback exists.
        test_engine = self._config.test_matching_engine
        rust_attempt: Any = None
        # Skip Rust matching when either tree is empty (file add/delete
        # lifecycle). The Rust language-agnostic matcher treats empty-root
        # ↔ content-root as a structural match, which produces a single
        # MODIFICATION instead of the correct ADDITION (or DELETION) for
        # the file-lifecycle case. Letting the Python oracle handle these
        # edge cases preserves the ADDITION/DELETION shape that downstream
        # tests and review UIs depend on.
        either_tree_empty = (
            not old_tree.children or not new_tree.children
        )
        use_rust_matching = (
            test_engine == "rust"
            or (
                test_engine is None
                and self._config.experimental_rust_core
                and not diagnostics.enabled
                and not either_tree_empty
            )
        )
        # ── 9-fin. Rust finalize routing (issue #57) ──────────────────────────
        # Certified languages skip the python edit-script/refinement/presentation stages
        # entirely: the Rust core runs the certified batch's refine+finalize from the
        # semantic trees and returns the finished review. Eligibility is independent of
        # the matching-engine choice (issue #57 payoff, fallback-tier closure):
        # - empty-tree lifecycle is handled INSIDE Rust finalize (DELETION+ADDITION
        #   root pair, python parity), so either_tree_empty no longer excludes;
        # - enriched trees flow through routing (enrichment ran at stage 7b);
        # - registered diff analyzers run on the ROUTED diff below, mirroring 13.5.
        # Diagnostics mode routes too (issue #57 payoff, stage 4a): the shell-side
        # records (parse, trees, guardrails, finalize summary below) still fire; the
        # per-pass refinement trace is Rust-internal (INTENTDIFF_FINALIZE_DEBUG probe)
        # until #54 formalizes it into the recorder.
        rust_finalize_eligible = test_engine == "rust" or (
            test_engine is None and self._config.experimental_rust_core
        )
        if rust_finalize_eligible and language in _rust_finalize_languages():
            from intentdiff.rust_core import try_rust_finalize_review

            with profiler.phase("rust_finalize_review"):
                finalize_result = try_rust_finalize_review(
                    old_tree=old_tree,
                    new_tree=new_tree,
                    old_source=old_content,
                    new_source=new_content,
                    language=language,
                    config=self._config,
                    collect_trace=diagnostics.enabled,
                )
            if finalize_result is not None:
                diagnostics.record(
                    stage="finalize",
                    action="rust_finalize_review",
                    rule_id="rust_core.finalize_review_v1",
                    reason="routed per-stage finalize served the review",
                    metadata={
                        "engine_owner": "rust",
                        "change_count": len(finalize_result["changes"]),
                        "is_style_only": finalize_result["is_style_only"],
                    },
                )
                # Per-pass trace (issue #54): every probed refine/finalize pass reports
                # its surviving change count; the recorder mirrors the python pipeline's
                # per-stage observability on the Rust path.
                for entry in finalize_result.get("trace") or []:
                    diagnostics.record_count(
                        stage="finalize",
                        action=str(entry.get("pass", "")),
                        rule_id="rust_core.finalize_pass",
                        reason="rust finalize pass completed",
                        count=int(entry.get("changes_after", 0)),
                    )
                # Semantic invariances (css color / literal equivalence / yaml block↔flow / import
                # reorder / …) are equivalences that must hold REGARDLESS of routing, but this
                # short-circuit returns before the stage-12 apply_invariances. Run it here on the
                # routed changes so canonical-value "zero-change" edits collapse under routing too
                # (#57: unlocks json/yaml/css/python). No-op when no invariance fires.
                fin_changes = finalize_result["changes"]
                fin_is_style_only = finalize_result["is_style_only"]
                fin_invariant_groups: list[Any] = []
                fin_invariant_ignored: list[dict[str, Any]] = []
                if fin_changes:
                    _inv = apply_invariances(
                        fin_changes,
                        old_tree=old_tree,
                        new_tree=new_tree,
                        old_source=old_content,
                        new_source=new_content,
                        language=language,
                    )
                    if len(_inv.changes) != len(fin_changes) or _inv.change_groups:
                        fin_changes = _inv.changes
                        fin_invariant_groups = list(_inv.change_groups)
                        # The invariance's own IGNORED_STYLE groups ARE the style evidence — carry
                        # their rule_id/reason so metadata["ignored_style_changes"] leads with the
                        # specific reason (css.color.canonical_equivalence), not the generic
                        # source-equivalence fallback below.
                        for group in fin_invariant_groups:
                            diagnostics.record_group(stage="invariance", group=group)
                        fin_invariant_ignored = [
                            {"rule_id": g.rule_id, **dict(g.metadata)}
                            for g in fin_invariant_groups
                            if g.kind == ChangeGroupKind.IGNORED_STYLE
                        ]
                        if not fin_changes and old_content != new_content:
                            fin_is_style_only = True
                # Generic files are line-oriented from the user's point of view
                # (#57 generic routing, mirroring the stage-12 orchestration): the routed
                # finalize's parser-token churn is REPLACED wholesale by the Rust text
                # review's stable line/character spans, then the (Rust-first) markdown
                # section passes run for .md filenames. Parser-derived groups are dropped
                # with the churn — carrying them forward invents fake pairings between
                # unrelated lines.
                fin_ignored_override: list[dict[str, Any]] | None = None
                if language.lower() == "generic":
                    generic_presented = normalize_generic_text_for_review(
                        fin_changes,
                        old_content,
                        new_content,
                    )
                    presented_generic = PresentationResult(
                        changes=generic_presented.changes,
                        change_groups=generic_presented.change_groups,
                        ignored_style_changes=[
                            *generic_presented.ignored_style_changes,
                        ],
                    )
                    presented_generic = _markdown_section_move_presentation(
                        presented_generic,
                        old_source=old_content,
                        new_source=new_content,
                        old_filename=filename,
                        new_filename=(
                            new_filename if new_filename is not None else filename
                        ),
                    )
                    presented_generic = _markdown_section_heading_rename_presentation(
                        presented_generic,
                        old_source=old_content,
                        new_source=new_content,
                        old_filename=filename,
                        new_filename=(
                            new_filename if new_filename is not None else filename
                        ),
                    )
                    fin_changes = presented_generic.changes
                    fin_invariant_groups = list(presented_generic.change_groups)
                    fin_ignored_override = list(presented_generic.ignored_style_changes)
                    finalize_result["change_groups"] = []
                    finalize_result["no_surviving_changes"] = False
                    # Mirror the stage-12 style-only resolution: identical sources (or
                    # whitespace-collapsed-equal trees) with no surviving line spans are a
                    # style-only diff, not "no changes". The Rust finalize's flag reflects
                    # its own suppressions, which the replacement just discarded.
                    if not fin_changes:

                        def _generic_norm(node: SemanticNode) -> tuple[Any, ...]:
                            label = " ".join(node.label.split())
                            return (
                                node.node_type,
                                label,
                                tuple(_generic_norm(c) for c in node.children),
                            )

                        fin_is_style_only = (
                            old_content == new_content
                            or _generic_norm(old_tree) == _generic_norm(new_tree)
                        )
                # Stage-12 style-only resolution, language-agnostic (markdown #44 exposed
                # it): ZERO surviving changes with identical (or whitespace-collapsed
                # tree-equal) sources is a style-only diff for every routed language —
                # the Rust finalize's flag only reflects its own suppressions.
                if not fin_changes and not fin_is_style_only:

                    def _routed_norm(node: SemanticNode) -> tuple[Any, ...]:
                        label = " ".join(node.label.split())
                        return (
                            node.node_type,
                            label,
                            tuple(_routed_norm(c) for c in node.children),
                        )

                    fin_is_style_only = (
                        old_content == new_content
                        or _routed_norm(old_tree) == _routed_norm(new_tree)
                    )
                metadata_fin: dict[str, Any] = {
                    "engine_owner": "rust",
                    "semantic_contract": "rust_finalize_review_v1",
                    "rust_core": {
                        "engine": "rust_finalize_review_v1",
                        "stage": "per_stage_finalize_routing",
                        "used": True,
                    },
                }
                # Context metadata the normal path attaches at stage 11 — the routed
                # return must carry the same contracts (cpp compile_commands pilot gap).
                if schema_metadata is not None:
                    metadata_fin["schema"] = schema_metadata
                if compile_metadata is not None:
                    metadata_fin["compile_commands"] = compile_metadata
                combined_ignored = (
                    fin_ignored_override
                    if fin_ignored_override is not None
                    else [
                        *fin_invariant_ignored,
                        *finalize_result["ignored_style_changes"],
                    ]
                )
                if combined_ignored:
                    metadata_fin["ignored_style_changes"] = combined_ignored
                if finalize_result["no_surviving_changes"]:
                    metadata_fin["no_surviving_changes"] = True
                routed_groups = [*finalize_result["change_groups"], *fin_invariant_groups]
                if not fin_changes and old_content != new_content and not fin_invariant_ignored:
                    # The normal path attaches a source-equivalence suppression group
                    # post-diff when a diff nets to zero changes but the sources differ
                    # (xml attribute reorder -> zero changes must still RECORD the
                    # suppression, not merely be absent). The routed short-circuit returns
                    # before that stage, so mirror it here.
                    style_evidence = build_style_only_evidence(
                        old_source=old_content,
                        new_source=new_content,
                        language=language,
                    )
                    for group in style_evidence.change_groups:
                        diagnostics.record_group(stage="invariance", group=group)
                    routed_groups.extend(style_evidence.change_groups)
                    if style_evidence.ignored_style_changes:
                        metadata_fin.setdefault("ignored_style_changes", []).extend(
                            style_evidence.ignored_style_changes
                        )
                routed = SemanticDiff(
                    old_filename=filename,
                    new_filename=new_filename if new_filename is not None else filename,
                    language=language,
                    changes=fin_changes,
                    change_groups=routed_groups,
                    has_semantic_changes=bool(fin_changes) and not fin_is_style_only,
                    is_style_only=fin_is_style_only,
                    metadata=metadata_fin,
                )
                routed = _apply_file_lifecycle_to_diff(routed, file_lifecycle)
                routed = apply_guardrails_to_diff(
                    routed,
                    old_tree=old_tree,
                    new_tree=new_tree,
                    old_source=old_content,
                    new_source=new_content,
                    config=self._config,
                    diagnostics=diagnostics,
                )
                # Diff-analyzer pass on the routed diff (stage 13.5 mirror; issue #57
                # payoff): analyzers are diff->diff Wasm transforms over the final
                # SemanticDiff JSON, so the routed review feeds them identically.
                with profiler.phase("diff_analyzers"):
                    routed = self._run_diff_analyzers_on_routed(
                        routed, language, filename, adaptive_fuel
                    )
                return _attach_run_telemetry(routed, engine_telemetry, diagnostics)
            # The Rust finalize declined (tree_too_large / backend unavailable). The
            # transitional python pipeline is retired (issue #57 payoff, stage 4b) —
            # degrade honestly to the coarse token-level diff, same as parse errors.
            logger.warning(
                "Rust finalize declined for %r (%s) — token-level fallback",
                filename,
                language,
            )
            _raise_rust_only_gate_error("rust finalize declined")
            diagnostics.record(
                stage="fallback",
                action="token_fallback",
                rule_id="pipeline.finalize_declined",
                reason="rust finalize declined; python pipeline retired (#57)",
            )
            fallback_metadata: dict[str, Any] = {
                "fallback_reason": "rust_finalize_declined",
            }
            if diagnostics.enabled:
                diagnostics.summary.update(
                    {"early_exit": "token_fallback", "final_change_count": None}
                )
                fallback_metadata["diagnostics"] = diagnostics.snapshot()
            fallback_diff = _token_fallback_diff(
                old_raw,
                new_raw,
                filename,
                new_filename if new_filename is not None else filename,
                language,
                metadata=fallback_metadata,
            )
            fallback_diff = _apply_file_lifecycle_to_diff(fallback_diff, file_lifecycle)
            return apply_guardrails_to_diff(
                fallback_diff,
                old_tree=None,
                new_tree=None,
                old_source=old_raw,
                new_source=new_raw,
                config=self._config,
                diagnostics=diagnostics,
            )
        if test_engine == "python":
            raise NotImplementedError(
                "test_matching_engine='python' was retired with the transitional "
                "python pipeline (issue #57); the Rust finalize routing serves every "
                "certified language."
            )

        # Rust core disabled (DiffConfig kill switch / INTENTDIFF_RUST_CORE=0): the
        # transitional python pipeline is retired (issue #57 payoff) — semantic
        # diffing REQUIRES the Rust core; degrade honestly to the token-level diff.
        logger.warning(
            "Rust core disabled for %r — token-level fallback (python pipeline retired)",
            filename,
        )
        _raise_rust_only_gate_error("rust core disabled")
        diagnostics.record(
            stage="fallback",
            action="token_fallback",
            rule_id="pipeline.rust_core_disabled",
            reason="rust core disabled; python pipeline retired (#57)",
        )
        disabled_metadata: dict[str, Any] = {
            "fallback_reason": "rust_core_disabled",
        }
        if diagnostics.enabled:
            diagnostics.summary.update(
                {"early_exit": "token_fallback", "final_change_count": None}
            )
            disabled_metadata["diagnostics"] = diagnostics.snapshot()
        disabled_diff = _token_fallback_diff(
            old_raw,
            new_raw,
            filename,
            new_filename if new_filename is not None else filename,
            language,
            metadata=disabled_metadata,
        )
        disabled_diff = _apply_file_lifecycle_to_diff(disabled_diff, file_lifecycle)
        return apply_guardrails_to_diff(
            disabled_diff,
            old_tree=None,
            new_tree=None,
            old_source=old_raw,
            new_source=new_raw,
            config=self._config,
            diagnostics=diagnostics,
        )

    def diff_stream_progressive(
        self,
        source: Source,
    ) -> Iterator[ChangeStreamEvent]:
        """Yield ``ChangeStreamEvent`` objects as each pipeline phase completes.

        Three phases are emitted in order:

        1. **STRUCTURAL** — one ``action="add"`` event per change after the
           GumTree edit script and move-promotion pass (stage 11).  These are
           available immediately, before the (potentially expensive) refactoring
           detection runs.

        2. **REFINED** — events emitted once refactoring detection (stage 12) has
           run.  Consumed raw changes are replaced by ``action="revise"`` events
           carrying a ``REFACTORING`` change and ``replaced_ids`` listing the
           node IDs of the Phase-1 changes they supersede.  Removed-but-not-
           replaced changes are emitted as ``action="remove"``.

        3. **FINAL** — events emitted after classification (stage 13) and any
           diff-analyzer plugins (stage 13.5) have run.  Emitted only when these
           passes modify the change list.

        Consumers that only care about the final result can wait until all events
        are consumed; consumers that want to display partial results can render
        STRUCTURAL changes immediately and update/remove them as REFINED and
        FINAL events arrive.
        """
        old_content, new_content, filename, language_hint = source.get_content()
        yield from self._run_pipeline_streaming(old_content, new_content, filename, language_hint)

    def _run_pipeline_streaming(
        self,
        old_content: str,
        new_content: str,
        filename: str,
        language_hint: str | None,
        *,
        new_filename: str | None = None,
    ) -> Iterator[ChangeStreamEvent]:
        """Internal generator — see ``diff_stream_progressive`` for the public API."""
        result = self._run_stages_1_to_11(
            old_content, new_content, filename, language_hint, new_filename=new_filename
        )

        if isinstance(result, SemanticDiff):
            # Early exit (cache hit / style-only / token fallback): emit all changes
            # as FINAL so consumers still receive a well-formed event stream.
            for change in result.changes:
                yield ChangeStreamEvent(
                    phase=ChangeStreamPhase.FINAL,
                    action="add",
                    change=change,
                )
            return

        raise AssertionError(
            "unreachable: _run_stages_1_to_11 always returns a SemanticDiff since the "
            "python pipeline retirement (issue #57 payoff, stage 4b)"
        )

    def _cached_process(
        self,
        parser: Any,
        input_: str,
        language: str,
        filename: str,
        fuel: int,
        *,
        cache_grammar_id: str | None = None,
    ) -> SemanticNode:
        """
        Call ``parser.process()`` with a parse-tree cache layer.

        On a cache hit the wasm plugin is skipped entirely and the cached
        ``SemanticNode`` tree is returned.  On a miss the result is stored
        before returning.
        """
        cache = self._cache
        if cache is None:
            raise RuntimeError("parse cache store is not configured")
        wasm_hash = self._wasm_hash_for(parser)
        grammar_id = cache_grammar_id or parser.grammar_id
        key = cache.parse_key(input_, grammar_id, wasm_hash)
        cached = cache.get_parse(key)
        if cached is not None:
            logger.debug("Parse cache hit for grammar=%r", grammar_id)
            return SemanticNode.model_validate_json(cached)

        node = parser.process(input_, language, filename, fuel=fuel)
        cache.put_parse(key, node.model_dump_json(), grammar_id=grammar_id)
        return node

    def _wasm_hash_for(self, parser: Any) -> str:
        """
        Return a short (16-hex-char) sha256 hash of the parser's Wasm binary.

        Computed once per grammar_id per process and memoised.  Falls back to
        ``"unknown"`` if the path cannot be read (e.g. in-memory test stubs).
        """
        grammar_id: str = parser.grammar_id
        if grammar_id in self._wasm_hash_cache:
            return self._wasm_hash_cache[grammar_id]
        try:
            wasm_bytes = Path(parser.wasm_path).read_bytes()
            h = hashlib.sha256(wasm_bytes).hexdigest()[:16]
        except (AttributeError, OSError):
            h = "unknown"
        self._wasm_hash_cache[grammar_id] = h
        return h

    def _parse(
        self,
        content: str,
        parser_adapter: Any,
        language: str,
        filename: str,
        *,
        profiler: _PhaseProfiler | None = None,
    ) -> str:
        """
        Prepare source for a parser plugin.

        FullParse parsers receive raw source. Legacy ``interpret-cst`` parsers
        are rejected so public paths cannot host parsing in Python.
        """
        if parser_adapter.parser_mode == "full-parse":
            return content  # plugin will parse internally

        raise PluginNotFoundError(
            "IntentDiff no longer supports Python-hosted InterpretCst parsers "
            "on public product paths. Rebuild or update parser "
            f"{getattr(parser_adapter, 'grammar_id', '<unknown>')!r} as a "
            "FullParse Rust/Wasm plugin."
        )
