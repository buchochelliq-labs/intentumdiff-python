"""
intentdiff.plugins.registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugin registry: discovery, caching, and dispatch.

Discovery
─────────
1. Built-in plugins are pre-registered via their entry-point callables
   (see ``pyproject.toml`` ``[project.entry-points]``).
2. Third-party plugins install themselves under the same entry-point groups.
3. On first use, ``PluginRegistry`` scans ``importlib.metadata`` entry points
   and loads each registered ``.wasm`` file.

Dispatch order (parsers)
────────────────────────
For each file, ``detect_parser`` iterates registered parsers in descending
priority order and calls ``detect_language``.  The first plugin that returns a
non-empty string wins.

Allowed-list filtering
──────────────────────
If ``DiffConfig.allowed_plugins`` is set, only those grammar IDs are consulted.
If ``DiffConfig.strict_plugins`` is True, an error is raised for any language
not handled by the allowed list.
"""

from __future__ import annotations

import importlib.metadata
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from intentdiff.core.models import DetectionResult, DiffConfig, LanguageInfoGroup
from intentdiff.plugins.adapter import (
    DiffAnalyzerAdapter,
    EnricherAdapter,
    ParserAdapter,
    RendererAdapter,
)
from intentdiff.plugins.exceptions import PluginNotFoundError
from intentdiff.plugins.language_metadata import fallback_language_info
from intentdiff.plugins.loader import LoadedPlugin, load_plugin

logger = logging.getLogger(__name__)

_WASM_PATH_METADATA_FIELD = "IntentDiff-Wasm-Path"

_PARSER_GROUP = "intentdiff.parsers"
_RENDERER_GROUP = "intentdiff.renderers"
_ENRICHER_GROUP = "intentdiff.enrichers"
_DIFF_ANALYZER_GROUP = "intentdiff.diff_analyzers"

# Entry-point callable type: returns the path to the .wasm file as str
EntryCallable = Callable[[], str]
PhaseRecorder = Callable[[str, float], None]

# First-party parser entry points that are intentionally not advertised until
# their parser contract is restored.  Keeping this host-side guard makes editable
# installs safe even when stale entry-point metadata still lists the plugin.
_DISABLED_BUILTIN_PARSER_ENTRYPOINTS: frozenset[str] = frozenset({"freebasic"})

# Normalised names of first-party packages whose entry-point callables are
# trusted plugin metadata.
_TRUSTED_PLUGIN_PACKAGE_NAMES: frozenset[str] = frozenset(
    {
        "intentdiff",
        "intentdiff-dbt",
        "intentdiff_dbt",
    }
)

_ENTRYPOINT_LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "asciidoc": ("asciidoc", "adoc"),
    "hcl": ("terraform", "hcl"),
    "databricks-workflow": ("databricks-workflow", "databricks"),
    "graphql": ("graphql", "gql"),
    "latex": ("latex", "tex"),
    "m": ("m", "dax"),
    "dax": ("dax", "m"),
    "ocaml": ("ocaml", "ml"),
    "po": ("po", "pot", "gettext"),
    "reasonml": ("reasonml", "re"),
    "typescript": ("typescript",),
    "tsx": ("tsx",),
    "wat": ("wat", "wast"),
    "yaml": ("yaml", "yml"),
}

_FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS: dict[str, str] = {
    # Source checkouts and long-lived editable environments can have stale
    # entry-point metadata after package identity changes. Keep built-ins
    # discoverable from the source tree instead of silently falling through to
    # the generic text parser.
    "python": "python_parser_entry",
    "sql": "sql_parser_entry",
    "generic": "generic_parser_entry",
    "graphql": "graphql_parser_entry",
    "gitignore": "gitignore_parser_entry",
    "ocaml": "ocaml_parser_entry",
    "reasonml": "reasonml_parser_entry",
    "latex": "latex_parser_entry",
    "asciidoc": "asciidoc_parser_entry",
    "po": "po_parser_entry",
    "javascript": "js_ts_parser_entry",
    "typescript": "js_ts_parser_entry",
    "tsx": "js_ts_parser_entry",
    "java": "java_parser_entry",
    "go": "go_parser_entry",
    "rust": "rust_parser_entry",
    "csharp": "csharp_parser_entry",
    "ruby": "ruby_parser_entry",
    "php": "php_parser_entry",
    "kotlin": "kotlin_parser_entry",
    "c": "cpp_parser_entry",
    "cpp": "cpp_parser_entry",
    "swift": "swift_parser_entry",
    "bash": "bash_parser_entry",
    "powershell": "powershell_parser_entry",
    "elixir": "elixir_parser_entry",
    "groovy": "groovy_parser_entry",
    "dart": "dart_parser_entry",
    "lua": "lua_parser_entry",
    "xml": "xml_parser_entry",
    "dockerfile": "dockerfile_parser_entry",
    "vbnet": "vbnet_parser_entry",
    "squirrel": "squirrel_parser_entry",
    "puppet": "puppet_parser_entry",
    "hcl": "terraform_parser_entry",
    "delphi": "delphi_parser_entry",
    "adf": "adf_parser_entry",
    "databricks-workflow": "databricks_parser_entry",
    "mdx": "mdx_parser_entry",
    "markdown": "markdown_parser_entry",
    "toml": "toml_parser_entry",
    "ini": "ini_parser_entry",
    "gomod": "gomod_parser_entry",
    "make": "make_parser_entry",
    "proto": "proto_parser_entry",
    "cmake": "cmake_parser_entry",
    "r": "r_parser_entry",
    "haskell": "haskell_parser_entry",
    "zig": "zig_parser_entry",
    "scala": "scala_parser_entry",
    "clojure": "clojure_parser_entry",
    "perl": "perl_parser_entry",
    "asm": "asm_parser_entry",
    "assemblyscript": "assemblyscript_parser_entry",
    "odin": "odin_parser_entry",
    "wat": "wat_parser_entry",
    "tsql": "tsql_parser_entry",
    "plsql": "plsql_parser_entry",
    "abap": "abap_parser_entry",
    "dax": "dax_parser_entry",
    "m": "dax_parser_entry",
    "sas": "sas_parser_entry",
    "qsharp": "qsharp_parser_entry",
    "postscript": "postscript_parser_entry",
    "css": "css_parser_entry",
    "json": "json_parser_entry",
    "yaml": "yaml_parser_entry",
    "html": "html_parser_entry",
    "scss": "scss_parser_entry",
    "vue": "vue_parser_entry",
    "svelte": "svelte_parser_entry",
    "astro": "astro_parser_entry",
}


@dataclass
class _ParserCatalogEntry:
    """Lightweight parser entry-point metadata.

    The catalog intentionally avoids instantiating Wasm components.  Normal
    diffing only loads the entry selected by filename/language/plugin-id
    shortlisting, while explicit inventory APIs can still load all parsers.
    """

    ep: importlib.metadata.EntryPoint
    wasm_path: str
    resolved_path: str
    entry_names: list[str]
    plugin_id: str
    package_name: str
    package_version: str
    author: str
    provenance: str
    trusted: bool
    adapter: ParserAdapter | None = None
    load_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def language_guesses(self) -> tuple[str, ...]:
        guesses: list[str] = []
        for name in self.entry_names:
            guesses.extend(_ENTRYPOINT_LANGUAGE_ALIASES.get(name, (name,)))
        return tuple(dict.fromkeys(guesses))


def _normalised_dist_name(dist: importlib.metadata.Distribution) -> str:
    raw_name: str = (dist.metadata.get("Name") or dist.name or "").lower()
    return raw_name.replace("-", "_")


def _is_trusted_entry_point(ep: importlib.metadata.EntryPoint) -> bool:
    dist = ep.dist
    if dist is None:
        return False
    trusted_names = {n.replace("-", "_") for n in _TRUSTED_PLUGIN_PACKAGE_NAMES}
    return _normalised_dist_name(dist) in trusted_names


# ---------------------------------------------------------------------------
# Entry-point stubs (builtins.py — see plugins/builtins.py)
# ---------------------------------------------------------------------------


def _wasm_dir() -> Path:
    """Return the directory containing built-in .wasm files."""
    return Path(__file__).parent.parent / "wasm"


# ---------------------------------------------------------------------------
# Secure wasm-path resolution
# ---------------------------------------------------------------------------


def _wasm_path_from_ep(ep: importlib.metadata.EntryPoint) -> str:
    """Resolve the ``.wasm`` path for a plugin entry point.

    **First-party plugins** (the core package and repo-maintained plugin
    packages) are trusted. Their entry-point callables are invoked to locate
    the wasm file.

    **Third-party plugins** must declare an ``IntentDiff-Wasm-Path`` field in their
    dist-info METADATA.  This field holds a path relative to the installed
    package root, resolvable via :func:`importlib.metadata.files` without
    importing any Python module.  If the field is absent, the plugin is
    rejected — falling back to ``ep.load()`` would execute arbitrary Python
    code before any Wasm sandboxing is in effect.

    Raises
    ------
    ValueError
        For third-party plugins that do not declare ``IntentDiff-Wasm-Path``, or
        when the declared path cannot be resolved on disk.
    """
    dist = ep.dist
    if dist is None:
        raise ValueError(
            f"Entry point {ep.name!r} has no associated distribution metadata. "
            "Cannot resolve wasm path safely."
        )

    # Trusted first-party plugins can use entry-point callables; untrusted
    # packages stay on declarative metadata-only path resolution.
    if _is_trusted_entry_point(ep):
        fn: EntryCallable = ep.load()
        return fn()

    # Third-party plugins: read the wasm path from declarative metadata only.
    wasm_rel: str | None = dist.metadata.get(_WASM_PATH_METADATA_FIELD)
    if not wasm_rel:
        raise ValueError(
            f"Third-party plugin {ep.name!r} (package {dist.metadata.get('Name')!r}) "
            f"does not declare an '{_WASM_PATH_METADATA_FIELD}' metadata field.  "
            "Importing the plugin's Python module is refused because it would "
            "execute arbitrary code before Wasm sandboxing is in effect.  "
            f"The plugin author must add '{_WASM_PATH_METADATA_FIELD}: <relative/path.wasm>' "
            "to their package metadata.  "
            "See: https://docs.intentdiff.dev/plugins/metadata"
        )

    # Locate the file via importlib.metadata without importing anything.
    for f in dist.files or []:
        f_str = str(f).replace("\\", "/")
        if f_str == wasm_rel.replace("\\", "/") or f.name == Path(wasm_rel).name:
            full = Path(str(dist.locate_file(f)))
            if full.exists():
                return str(full)

    raise ValueError(
        f"Plugin {ep.name!r}: '{_WASM_PATH_METADATA_FIELD}' declares {wasm_rel!r} "
        "but no matching file was found in the installed distribution."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PluginRegistry:
    """
    Thread-safe (at the loading stage) plugin registry.

    Instantiate once and reuse — ``LoadedPlugin`` objects are cached.
    """

    def __init__(self, config: DiffConfig | None = None) -> None:
        self._config = config or DiffConfig()
        self._parsers: list[ParserAdapter] | None = None
        self._parser_catalog: list[_ParserCatalogEntry] | None = None
        self._parser_load_errors: list[str] = []
        self._renderers: list[RendererAdapter] | None = None
        self._enrichers: list[EnricherAdapter] | None = None
        self._diff_analyzers: list[DiffAnalyzerAdapter] | None = None
        self._lock = threading.Lock()

    # ── Parser access ────────────────────────────────────────────────────────

    @property
    def parsers(self) -> list[ParserAdapter]:
        if self._parsers is None:
            with self._lock:
                if self._parsers is None:
                    load_errors: list[str] = []
                    self._parsers = _load_parsers(
                        fuel=self._config.plugin_fuel,
                        load_errors=load_errors,
                    )
                    self._parser_load_errors = load_errors
        return self._parsers

    def parser_load_failure_summary(self) -> str | None:
        """
        Return a user-facing summary when parser discovery has completely failed.

        Unsupported files should still be skipped quietly, but a repository
        review with zero loaded parsers is almost always an environment or
        dependency problem.  Surfacing this state prevents VS Code from showing
        a misleading "No semantic changes" result when every Wasm parser was
        rejected during startup.
        """
        if self._parsers:
            return None
        if self._parser_catalog is not None and any(entry.adapter for entry in self._parser_catalog):
            return None
        if not self._parser_load_errors:
            if self._parsers is None:
                return None
            return (
                "No parser plugins are installed or discoverable. Semantic review "
                "cannot run until IntentDiff is installed with its parser "
                "entry points."
            )
        sample = "; ".join(self._parser_load_errors[:5])
        remaining = len(self._parser_load_errors) - 5
        if remaining > 0:
            sample = f"{sample}; and {remaining} more"
        return (
            "No parser plugins could be loaded for the reviewed files. Semantic review "
            "cannot run until the parser environment is fixed. If this happened after "
            "upgrading dependencies, run 'uv sync' or reinstall intentdiff so "
            "the runtime matches the lockfile. Parser load errors: "
            f"{sample}"
        )

    def language_ids(self) -> list[str]:
        """Return parser language IDs reported by loaded parser plugins."""
        allowed = set(self._config.allowed_plugins or [])
        ids: set[str] = set()
        for parser in self.parsers:
            if allowed and parser.grammar_id not in allowed:
                continue
            ids.update(parser.language_ids)
        return sorted(ids)

    def language_info(self) -> list[LanguageInfoGroup]:
        """Return parser metadata grouped by language ID."""
        allowed = set(self._config.allowed_plugins or [])
        grouped: dict[str, list] = {}
        for parser in self.parsers:
            if allowed and parser.grammar_id not in allowed:
                continue
            for info in parser.language_info:
                grouped.setdefault(info.language_id, []).append(info)

        result: list[LanguageInfoGroup] = []
        for language, plugins in grouped.items():
            sorted_plugins = sorted(
                plugins,
                key=lambda info: (-info.priority, info.plugin_id),
            )
            result.append(
                LanguageInfoGroup(
                    language=language,
                    selected_plugin_id=sorted_plugins[0].plugin_id,
                    plugins=sorted_plugins,
                )
            )
        return sorted(result, key=lambda item: item.language)

    def get_parser_by_id(
        self,
        plugin_id: str,
        *,
        language: str | None = None,
    ) -> ParserAdapter:
        """Return a parser by opaque plugin ID, optionally requiring language support."""
        for parser in self.parsers:
            if parser.plugin_id != plugin_id:
                continue
            if language is not None and language not in parser.language_ids:
                break
            return parser
        raise PluginNotFoundError(language or plugin_id)

    def _catalog(self, phase_recorder: PhaseRecorder | None = None) -> list[_ParserCatalogEntry]:
        if self._parser_catalog is None:
            with self._lock:
                if self._parser_catalog is None:
                    load_errors: list[str] = []
                    with _record_phase(phase_recorder, "parser_entrypoint_discovery"):
                        self._parser_catalog = _discover_parser_catalog(load_errors=load_errors)
                    self._parser_load_errors.extend(load_errors)
        return self._parser_catalog

    def _candidate_entries(
        self,
        filename: str,
        *,
        language_hint: str | None = None,
        plugin_id: str | None = None,
        candidates: list[str] | None = None,
        phase_recorder: PhaseRecorder | None = None,
    ) -> list[_ParserCatalogEntry]:
        with _record_phase(phase_recorder, "parser_candidate_shortlist"):
            entries = self._catalog(phase_recorder)
            language_filter = set(candidates or [])
            if language_hint:
                language_filter.add(language_hint)

            result: list[_ParserCatalogEntry] = []
            for entry in entries:
                if plugin_id is not None:
                    if plugin_id == entry.plugin_id or plugin_id in entry.entry_names:
                        result.append(entry)
                    continue
                if language_filter:
                    if language_filter.intersection(entry.language_guesses):
                        result.append(entry)
                    continue
                if _entry_matches_filename(entry, filename):
                    result.append(entry)

            if result or plugin_id is not None or language_filter:
                # The generic parser is the designated FALLBACK: when a specific parser
                # also matched the filename (CMakeLists.txt matches both generic's .txt
                # and the cmake plugin), catalog order must not let generic claim the
                # file first — it detect-claims everything.
                result.sort(key=lambda e: "generic" in e.language_guesses)
                return result
            return list(entries)

    def _load_catalog_entry(
        self,
        entry: _ParserCatalogEntry,
        *,
        phase_recorder: PhaseRecorder | None = None,
    ) -> ParserAdapter:
        if entry.adapter is not None:
            return entry.adapter
        with entry.lock:
            if entry.adapter is not None:
                return entry.adapter
            with _record_phase(phase_recorder, "parser_plugin_instantiation"):
                try:
                    plugin = load_plugin(
                        entry.wasm_path,
                        self._config.plugin_fuel,
                        trusted=entry.trusted,
                    )
                    adapter = ParserAdapter(plugin)
                    _assign_catalog_metadata(adapter, entry)
                    entry.adapter = adapter
                    entry.load_error = None
                    logger.debug(
                        "Loaded parser plugin: %s (%s)",
                        ",".join(entry.entry_names),
                        entry.wasm_path,
                    )
                    return adapter
                except Exception as exc:
                    message = f"{entry.entry_names[0]}: {exc}"
                    entry.load_error = message
                    if message not in self._parser_load_errors:
                        self._parser_load_errors.append(message)
                    logger.warning(
                        "Failed to load parser plugin %r: %s",
                        entry.entry_names[0],
                        exc,
                    )
                    raise

    def detect_parser(
        self,
        filename: str,
        content: str,
        language_hint: str | None = None,
        plugin_id: str | None = None,
        phase_recorder: PhaseRecorder | None = None,
    ) -> tuple[ParserAdapter, str]:
        """
        Return ``(parser, language)`` for a given file.

        If ``language_hint`` is supplied it overrides detection; we still
        find the parser by grammar-id match.

        Raises ``PluginNotFoundError`` if no parser can handle the file.
        """
        allowed = self._config.allowed_plugins

        if plugin_id:
            for entry in self._candidate_entries(
                filename,
                language_hint=language_hint,
                plugin_id=plugin_id,
                phase_recorder=phase_recorder,
            ):
                try:
                    parser = self._load_catalog_entry(entry, phase_recorder=phase_recorder)
                    if allowed is not None and parser.grammar_id not in allowed:
                        continue
                    if language_hint:
                        if language_hint in parser.language_ids:
                            return parser, language_hint
                        continue
                    with _record_phase(phase_recorder, "parser_plugin_language_detection"):
                        lang = parser.detect_language(filename, content[:2048])
                    if lang:
                        return parser, lang
                except Exception as exc:
                    logger.debug(
                        "Skipping parser candidate %s after load failure: %s",
                        ",".join(entry.entry_names),
                        exc,
                    )
                    continue
            raise PluginNotFoundError(plugin_id, filename)

        if language_hint:
            for entry in self._candidate_entries(
                filename,
                language_hint=language_hint,
                phase_recorder=phase_recorder,
            ):
                try:
                    parser = self._load_catalog_entry(entry, phase_recorder=phase_recorder)
                except Exception as exc:
                    logger.debug(
                        "Skipping parser candidate %s after load failure: %s",
                        ",".join(entry.entry_names),
                        exc,
                    )
                    continue
                if allowed is not None and parser.grammar_id not in allowed:
                    continue
                if language_hint in parser.language_ids:
                    return parser, language_hint
                if language_hint in entry.language_guesses and "generic" in parser.language_ids:
                    return parser, language_hint
            if self._config.strict_plugins:
                raise PluginNotFoundError(language_hint, filename)

        primary_entries = self._candidate_entries(filename, phase_recorder=phase_recorder)
        matched_paths: set[str] = set()
        for entries in (
            primary_entries,
            [entry for entry in self._catalog() if entry not in primary_entries],
        ):
            for entry in entries:
                if entry.resolved_path in matched_paths:
                    continue
                matched_paths.add(entry.resolved_path)
                try:
                    parser = self._load_catalog_entry(entry, phase_recorder=phase_recorder)
                except Exception as exc:
                    logger.debug(
                        "Skipping parser candidate %s after load failure: %s",
                        ",".join(entry.entry_names),
                        exc,
                    )
                    continue
                if allowed is not None and parser.grammar_id not in allowed:
                    continue
                with _record_phase(phase_recorder, "parser_plugin_language_detection"):
                    lang = parser.detect_language(filename, content[:2048])
                if lang:
                    return parser, lang
            if entries is primary_entries and primary_entries:
                break

        raise PluginNotFoundError("unknown", filename)

    def detect_by_content(
        self,
        content: str,
        candidates: list[str] | None = None,
        preferred_plugins: dict[str, str] | None = None,
        plugin_id: str | None = None,
    ) -> list[DetectionResult]:
        """Return all parsers that claim to handle *content*, ranked by priority.

        Parameters
        ----------
        content:
            Source code to identify (first 4096 bytes are inspected).
        candidates:
            Optional pre-filter.  When supplied, only parsers whose
            ``language_ids`` overlap with *candidates* are consulted.
        """
        if plugin_id:
            parser = self.get_parser_by_id(plugin_id)
            allowed = self._config.allowed_plugins
            if allowed is not None and parser.grammar_id not in allowed:
                raise PluginNotFoundError(plugin_id)
            if candidates and not any(c in parser.language_ids for c in candidates):
                raise PluginNotFoundError(plugin_id)
            lang = parser.detect_language("", content[:4096])
            if not lang or (candidates and lang not in candidates):
                raise PluginNotFoundError(plugin_id)
            return [
                DetectionResult(
                    language=lang,
                    grammar_id=parser.grammar_id,
                    confidence=1.0,
                )
            ]

        ranked = sorted(self.parsers, key=lambda p: p.priority, reverse=True)
        allowed = self._config.allowed_plugins
        raw_results: list[tuple[str, str, int, bool]] = []
        for parser in ranked:
            if allowed is not None and parser.grammar_id not in allowed:
                continue
            if candidates and not any(c in parser.language_ids for c in candidates):
                continue
            lang = parser.detect_language("", content[:4096])
            if lang:
                preferred = bool(
                    preferred_plugins
                    and preferred_plugins.get(lang) == parser.plugin_id
                )
                raw_results.append((lang, parser.grammar_id, parser.priority, preferred))
        raw_results.sort(key=lambda item: (not item[3], -item[2], item[0], item[1]))
        results: list[DetectionResult] = []
        for rank, (lang, grammar_id, _priority, _preferred) in enumerate(raw_results):
            results.append(
                DetectionResult(
                    language=lang,
                    grammar_id=grammar_id,
                    confidence=round(1.0 / (rank + 1), 3),
                )
            )
        return results

    def example_for(self, language: str, plugin_id: str | None = None) -> dict[str, str] | None:
        """Return the ``{old, new}`` playground example from the parser that
        owns *language*, or ``None`` if no parser claims it or the parser
        has no example."""
        if plugin_id:
            parser = self.get_parser_by_id(plugin_id, language=language)
            return parser.playground_example(language)
        for parser in sorted(self.parsers, key=lambda p: p.priority, reverse=True):
            if language in parser.language_ids:
                return parser.playground_example(language)
        return None

    # ── Renderer access ──────────────────────────────────────────────────────

    @property
    def renderers(self) -> list[RendererAdapter]:
        if self._renderers is None:
            with self._lock:
                if self._renderers is None:
                    self._renderers = _load_renderers(fuel=self._config.plugin_fuel)
        return self._renderers

    def get_renderer(self, format_name: str) -> RendererAdapter:
        """Return the highest-priority renderer for ``format_name``."""
        candidates = sorted(self.renderers, key=lambda r: r.priority, reverse=True)
        for renderer in candidates:
            if renderer.format_name == format_name:
                return renderer
        raise KeyError(
            f"No renderer found for format {format_name!r}. "
            f"Available: {[r.format_name for r in self.renderers]}"
        )

    # ── Enricher access ──────────────────────────────────────────────────────

    @property
    def enrichers(self) -> list[EnricherAdapter]:
        if self._enrichers is None:
            with self._lock:
                if self._enrichers is None:
                    self._enrichers = _load_enrichers(fuel=self._config.plugin_fuel)
        return self._enrichers

    def get_enrichers(self, language: str) -> list[EnricherAdapter]:
        """Return enrichers that handle ``language``, sorted by priority descending."""
        return sorted(
            [e for e in self.enrichers if language in e.language_ids],
            key=lambda e: e.priority,
            reverse=True,
        )

    # ── Diff-analyzer access ───────────────────────────────────────────────

    @property
    def diff_analyzers(self) -> list[DiffAnalyzerAdapter]:
        if self._diff_analyzers is None:
            with self._lock:
                if self._diff_analyzers is None:
                    self._diff_analyzers = _load_diff_analyzers(
                        fuel=self._config.plugin_fuel
                    )
        return self._diff_analyzers

    def get_diff_analyzers(self, language: str) -> list[DiffAnalyzerAdapter]:
        """Return diff analyzers that handle ``language``, sorted by priority descending."""
        return sorted(
            [a for a in self.diff_analyzers if language in a.language_ids],
            key=lambda a: a.priority,
            reverse=True,
        )


# ---------------------------------------------------------------------------
# Internal loading helpers
# ---------------------------------------------------------------------------


def _provenance(ep: importlib.metadata.EntryPoint) -> str:
    """Return a human-readable provenance string for an entry point."""
    dist = ep.dist
    if dist is None:
        return ""
    meta = dist.metadata
    name = meta.get("Name") or dist.name
    version = meta.get("Version") or "?"
    author = meta.get("Author") or ""
    if not author:
        # RFC 822 Author-email header may contain "Name <email>" format
        author_email = meta.get("Author-email") or ""
        if "<" in author_email:
            author = author_email.split("<")[0].strip().strip('"')
        elif author_email:
            author = author_email
    if author:
        return f"{name} {version} ({author})"
    return f"{name} {version}"


def _distribution_metadata(ep: importlib.metadata.EntryPoint) -> dict[str, str]:
    """Return package metadata used for host-owned plugin info fields."""
    dist = ep.dist
    if dist is None:
        return {"name": "", "version": "", "author": ""}
    meta = dist.metadata
    name = meta.get("Name") or dist.name or ""
    version = meta.get("Version") or ""
    author = meta.get("Author") or ""
    if not author:
        author_email = meta.get("Author-email") or ""
        if "<" in author_email:
            author = author_email.split("<")[0].strip().strip('"')
        elif author_email:
            author = author_email
    return {"name": name, "version": version, "author": author}


def _slug(value: str) -> str:
    text = value.strip().lower().replace("_", "-")
    return re.sub(r"[^a-z0-9.-]+", "-", text).strip("-") or "plugin"


def _make_plugin_id(
    ep: importlib.metadata.EntryPoint,
    plugin_key: str,
    used_ids: set[str],
) -> str:
    meta = _distribution_metadata(ep)
    base = f"{_slug(meta['name'] or 'unknown')}:{_slug(plugin_key)}:{_slug(ep.name)}"
    plugin_id = base
    suffix = 2
    while plugin_id in used_ids:
        plugin_id = f"{base}:{suffix}"
        suffix += 1
    used_ids.add(plugin_id)
    return plugin_id


def _assign_parser_metadata(
    adapter: ParserAdapter,
    ep: importlib.metadata.EntryPoint,
    used_ids: set[str],
) -> None:
    meta = _distribution_metadata(ep)
    adapter.package_name = meta["name"]
    adapter.package_version = meta["version"]
    adapter.author = meta["author"]
    adapter.provenance = _provenance(ep)
    # Do not call guest exports during discovery. Very low fuel configurations
    # intentionally exercise runtime exhaustion, and discovery should not drop
    # every parser before the parse attempt gets a chance to fail clearly.
    adapter.plugin_id = _make_plugin_id(ep, ep.name, used_ids)


@contextmanager
def _record_phase(
    recorder: PhaseRecorder | None,
    name: str,
) -> Iterator[None]:
    if recorder is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        recorder(name, (time.perf_counter() - started) * 1000)


def _assign_catalog_metadata(adapter: ParserAdapter, entry: _ParserCatalogEntry) -> None:
    adapter.package_name = entry.package_name
    adapter.package_version = entry.package_version
    adapter.author = entry.author
    adapter.provenance = entry.provenance
    adapter.plugin_id = entry.plugin_id


def _fallback_info_for_catalog(
    entry: _ParserCatalogEntry,
    language_id: str,
):
    return fallback_language_info(
        language_id,
        plugin_id=entry.plugin_id,
        grammar_id=entry.entry_names[0],
        priority=0,
        is_trusted=entry.trusted,
        provenance=entry.provenance,
        author=entry.author,
        plugin_version=entry.package_version,
    )


def _entry_matches_filename(entry: _ParserCatalogEntry, filename: str) -> bool:
    if not filename:
        return False
    name = Path(filename).name.lower()
    full = filename.replace("\\", "/").lower()
    for language_id in entry.language_guesses:
        info = _fallback_info_for_catalog(entry, language_id)
        if name == info.default_filename.lower() or full.endswith(
            "/" + info.default_filename.lower()
        ):
            return True
        for extension in info.language_file_extensions:
            normalized = extension.lower()
            if normalized.startswith("."):
                if name.endswith(normalized):
                    return True
            elif name == normalized:
                return True
    return False


def _discover_parser_catalog(
    *,
    load_errors: list[str] | None = None,
) -> list[_ParserCatalogEntry]:
    """Discover parser entry points without instantiating Wasm plugins."""
    entries: list[_ParserCatalogEntry] = []
    by_path: dict[str, _ParserCatalogEntry] = {}
    used_ids: set[str] = set()
    discovered = list(importlib.metadata.entry_points(group=_PARSER_GROUP))
    for ep in discovered:
        try:
            if ep.name in _DISABLED_BUILTIN_PARSER_ENTRYPOINTS and _is_trusted_entry_point(ep):
                logger.debug("Skipping disabled first-party parser entry point: %s", ep.name)
                continue
            wasm_path = _wasm_path_from_ep(ep)
            resolved = str(Path(wasm_path).resolve())
            existing = by_path.get(resolved)
            if existing is not None:
                if ep.name not in existing.entry_names:
                    existing.entry_names.append(ep.name)
                logger.debug("Cataloged duplicate parser entry point: %s (%s)", ep.name, wasm_path)
                continue

            meta = _distribution_metadata(ep)
            entry = _ParserCatalogEntry(
                ep=ep,
                wasm_path=wasm_path,
                resolved_path=resolved,
                entry_names=[ep.name],
                plugin_id=_make_plugin_id(ep, ep.name, used_ids),
                package_name=meta["name"],
                package_version=meta["version"],
                author=meta["author"],
                provenance=_provenance(ep),
                trusted=_is_trusted_entry_point(ep),
            )
            entries.append(entry)
            by_path[resolved] = entry
            logger.debug("Cataloged parser plugin: %s (%s)", ep.name, wasm_path)
        except Exception as exc:
            message = f"{ep.name}: {exc}"
            if load_errors is not None:
                load_errors.append(message)
            logger.warning("Failed to catalog parser plugin %r: %s", ep.name, exc)
    if all(isinstance(ep, importlib.metadata.EntryPoint) for ep in discovered):
        _add_first_party_parser_entrypoint_fallbacks(entries, by_path, used_ids)
    return entries


def _add_first_party_parser_entrypoint_fallbacks(
    entries: list[_ParserCatalogEntry],
    by_path: dict[str, _ParserCatalogEntry],
    used_ids: set[str],
) -> None:
    existing_names = {name for entry in entries for name in entry.entry_names}
    if set(_FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS).issubset(existing_names):
        return
    from intentdiff.plugins import builtins

    for name, callable_name in _FIRST_PARTY_PARSER_ENTRYPOINT_FALLBACKS.items():
        if name in existing_names:
            continue
        entry_callable = getattr(builtins, callable_name, None)
        if entry_callable is None:
            continue
        wasm_path = entry_callable()
        if not Path(wasm_path).exists():
            continue
        resolved = str(Path(wasm_path).resolve())
        existing = by_path.get(resolved)
        if existing is not None:
            existing.entry_names.append(name)
            continue
        ep = importlib.metadata.EntryPoint(
            name=name,
            value=f"intentdiff.plugins.builtins:{callable_name}",
            group=_PARSER_GROUP,
        )
        plugin_id = f"intentdiff:{name}:{name}"
        suffix = 2
        while plugin_id in used_ids:
            plugin_id = f"intentdiff:{name}:{name}:{suffix}"
            suffix += 1
        used_ids.add(plugin_id)
        entry = _ParserCatalogEntry(
            ep=ep,
            wasm_path=wasm_path,
            resolved_path=resolved,
            entry_names=[name],
            plugin_id=plugin_id,
            package_name="intentdiff",
            package_version="",
            author="",
            provenance="IntentDiff built-in fallback",
            trusted=True,
        )
        entries.append(entry)
        by_path[resolved] = entry
        logger.debug("Cataloged fallback parser plugin: %s (%s)", name, wasm_path)


def _load_parsers(
    fuel: int = 10_000_000,
    *,
    load_errors: list[str] | None = None,
) -> list[ParserAdapter]:
    """Discover and instantiate all registered parser plugins.

    Entry points for multi-language parsers (e.g. javascript/typescript/tsx)
    each point at the same wasm file.  We deduplicate by resolved wasm path so
    that each physical plugin is loaded exactly once.
    """
    adapters: list[ParserAdapter] = []
    for entry in _discover_parser_catalog(load_errors=load_errors):
        try:
            plugin = load_plugin(entry.wasm_path, fuel, trusted=entry.trusted)
            adapter = ParserAdapter(plugin)
            _assign_catalog_metadata(adapter, entry)
            adapters.append(adapter)
            logger.debug(
                "Loaded parser plugin: %s (%s)",
                ",".join(entry.entry_names),
                entry.wasm_path,
            )
        except Exception as exc:
            message = f"{entry.entry_names[0]}: {exc}"
            if load_errors is not None:
                load_errors.append(message)
            logger.warning("Failed to load parser plugin %r: %s", entry.entry_names[0], exc)
    return adapters


def _load_renderers(fuel: int = 10_000_000) -> list[RendererAdapter]:
    """Discover and instantiate all registered renderer plugins."""
    adapters: list[RendererAdapter] = []
    for ep in importlib.metadata.entry_points(group=_RENDERER_GROUP):
        try:
            wasm_path = _wasm_path_from_ep(ep)
            plugin = load_plugin(wasm_path, fuel, trusted=_is_trusted_entry_point(ep))
            adapter = RendererAdapter(plugin)
            adapter.provenance = _provenance(ep)
            adapters.append(adapter)
            logger.debug("Loaded renderer plugin: %s (%s)", ep.name, wasm_path)
        except Exception as exc:
            logger.warning("Failed to load renderer plugin %r: %s", ep.name, exc)
    return adapters


def _load_enrichers(fuel: int = 10_000_000) -> list[EnricherAdapter]:
    """Discover and instantiate all registered enricher plugins."""
    adapters: list[EnricherAdapter] = []
    for ep in importlib.metadata.entry_points(group=_ENRICHER_GROUP):
        try:
            wasm_path = _wasm_path_from_ep(ep)
            plugin = load_plugin(wasm_path, fuel, trusted=_is_trusted_entry_point(ep))
            adapter = EnricherAdapter(plugin)
            adapter.provenance = _provenance(ep)
            adapters.append(adapter)
            logger.debug("Loaded enricher plugin: %s (%s)", ep.name, wasm_path)
        except Exception as exc:
            logger.warning("Failed to load enricher plugin %r: %s", ep.name, exc)
    return adapters


def _load_diff_analyzers(fuel: int = 10_000_000) -> list[DiffAnalyzerAdapter]:
    """Discover and instantiate all registered diff-analyzer plugins."""
    adapters: list[DiffAnalyzerAdapter] = []
    for ep in importlib.metadata.entry_points(group=_DIFF_ANALYZER_GROUP):
        try:
            wasm_path = _wasm_path_from_ep(ep)
            plugin = load_plugin(wasm_path, fuel, trusted=_is_trusted_entry_point(ep))
            adapter = DiffAnalyzerAdapter(plugin)
            adapter.provenance = _provenance(ep)
            adapters.append(adapter)
            logger.debug("Loaded diff-analyzer plugin: %s (%s)", ep.name, wasm_path)
        except Exception as exc:
            logger.warning("Failed to load diff-analyzer plugin %r: %s", ep.name, exc)
    return adapters
