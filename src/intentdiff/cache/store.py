"""
intentdiff.cache.store
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Abstract base class for cache stores and shared key-generation utilities.

Key generation is Rust-authoritative (#101, A2.2): the deterministic, length-prefixed
SHA-256 cache keys are computed in the core (``cache.rs``), so every binding — and the
native SqliteCacheStore — agree on the exact key for a given input.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from intentdiff.rust_core import (
    cache_diff_key,
    cache_hover_map_key,
    cache_make_key,
    cache_parse_key,
)


def _make_key(*parts: str) -> str:
    """
    SHA-256 hex digest of length-prefixed parts (Rust-authoritative, #101).

    Each part is encoded as ``<4-byte little-endian length><utf-8 bytes>``, making
    the encoding injective — ``_make_key("a\\x00b")`` and ``_make_key("a", "b")``
    hash differently even though both contain a null byte.
    """
    return cache_make_key(list(parts))


class CacheStore(ABC):
    """
    Abstract cache store for parse trees and diff results.

    Subclasses must implement the four read/write methods.  The
    ``record_diff`` hook is optional — the default is a no-op so that the
    SQLite store can be used without DuckDB.
    """

    # ── Key construction (Rust-authoritative) ─────────────────────────────

    def parse_key(self, filtered_cst_or_content: str, grammar_id: str, wasm_hash: str) -> str:
        """
        Cache key for a single-file parser result.

        *filtered_cst_or_content* is the exact bytes fed to the plugin's
        ``process()`` call (trivia-stripped CST JSON for interpret-cst parsers,
        raw source for full-parse parsers).
        """
        return cache_parse_key(filtered_cst_or_content, grammar_id, wasm_hash)

    def diff_key(
        self,
        old_preprocessed: str,
        new_preprocessed: str,
        grammar_id: str,
        wasm_hash: str,
    ) -> str:
        """
        Cache key for a full-pipeline SemanticDiff result.

        Uses the preprocessed (but not trivia-stripped) source so that the
        key can be computed before the expensive parse steps.
        """
        return cache_diff_key(old_preprocessed, new_preprocessed, grammar_id, wasm_hash)

    # ── Parse cache ───────────────────────────────────────────────────────

    @abstractmethod
    def get_parse(self, key: str) -> str | None:
        """Return cached SemanticNode JSON string or ``None`` on miss."""

    @abstractmethod
    def put_parse(self, key: str, value: str, grammar_id: str = "") -> None:
        """Store SemanticNode JSON string under *key*."""

    # ── Diff cache ────────────────────────────────────────────────────────

    @abstractmethod
    def get_diff(self, key: str) -> str | None:
        """Return cached SemanticDiff JSON string or ``None`` on miss."""

    @abstractmethod
    def put_diff(
        self,
        key: str,
        value: str,
        language: str = "",
        old_filename: str = "",
        new_filename: str = "",
    ) -> None:
        """Store SemanticDiff JSON string under *key*."""

    # ── Symbol-index cache ────────────────────────────────────────────────

    def get_symbol_index(self, cache_key: str) -> tuple[str, str] | None:
        """
        Return ``(symbols_json, refs_json)`` for a previously stored index,
        or ``None`` on a cache miss.

        The default implementation is a no-op (always returns ``None``).
        """
        return None

    def put_symbol_index(
        self,
        cache_key: str,
        symbols_json: str,
        refs_json: str,
        file_count: int = 0,
    ) -> None:
        """
        Persist a built symbol index keyed by *cache_key*.

        The default implementation is a no-op.
        """

    # ── Hover-map cache ───────────────────────────────────────────────────

    def hover_map_key(self, content: str, language: str) -> str:
        """
        Cache key for the LSP hover-type map of a single file.

        Keyed by the SHA-256 of the file *content* and its *language* so that
        an unchanged file always hits the same cache entry regardless of path.
        """
        return cache_hover_map_key(content, language)

    def get_hover_map(self, key: str) -> dict[str, str] | None:
        """
        Return the cached ``{node_id: type_string}`` mapping or ``None``.

        The default implementation is a no-op (always returns ``None``).
        """
        return None

    def put_hover_map(self, key: str, value: dict[str, str]) -> None:
        """
        Persist a hover-type map keyed by *key*.

        The default implementation is a no-op.
        """

    # ── Administrative ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """
        Return a dict with cache statistics (row counts and sizes per table).

        The default implementation returns an empty dict.
        """
        return {}

    def clear(
        self,
        parse: bool = True,
        diff: bool = True,
        index: bool = True,
    ) -> None:
        """
        Delete cached entries, optionally scoped to individual tables.

        The default implementation is a no-op.
        """

    # ── Analytics hook ────────────────────────────────────────────────────

    def record_diff(self, diff_json: str) -> None:
        """
        Record a completed SemanticDiff for historical analytics.

        The default implementation is a no-op.  Override in stores that
        support analytics (e.g. ``DuckDBAnalyticsStore``).
        """

    # ── Lifecycle ─────────────────────────────────────────────────────────

    @abstractmethod
    def close(self) -> None:
        """Release any held resources (connections, file handles, etc.)."""

    def __enter__(self) -> "CacheStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
