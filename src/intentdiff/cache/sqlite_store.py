"""
intentdiff.cache.sqlite_store
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SQLite-backed cache for parse trees and diff results — a thin delegator over the
native Rust store (#101, A2.2).

The schema, gzip-compressed BLOB values, WAL mode, TTL + size-based eviction,
per-table hit/miss metrics, and B608-safe dynamic SQL all live in the core
(``crates/rust-core-host/src/cache.rs``). Python keeps only the CacheStore-shaped
API and the JSON marshalling for the compound return types. The deterministic key
methods (``parse_key`` / ``diff_key`` / ``hover_map_key``) are inherited from
``CacheStore`` (also Rust-backed).
"""

from __future__ import annotations

import json
from pathlib import Path

from intentdiff.cache.store import CacheStore
from intentdiff.rust_core import sqlite_cache_store


class SqliteCacheStore(CacheStore):
    """
    Disk-persistent cache backed by a local SQLite database (native Rust store).

    Parameters
    ----------
    path:
        Path to the ``.db`` file.  Parent directories are created if needed.
    ttl_days:
        Entries older than this many days are deleted on construction.
    max_mb:
        When the combined compressed size exceeds this limit the oldest ~20 %
        of rows are evicted.
    """

    def __init__(self, path: Path | str, ttl_days: int = 30, max_mb: int = 500) -> None:
        self._rust = sqlite_cache_store(str(path), ttl_days, max_mb)

    # ── Parse / diff cache ────────────────────────────────────────────────

    def get_parse(self, key: str) -> str | None:
        return self._rust.get_parse(key)

    def put_parse(self, key: str, value: str, grammar_id: str = "") -> None:
        self._rust.put_parse(key, value, grammar_id)

    def get_diff(self, key: str) -> str | None:
        return self._rust.get_diff(key)

    def put_diff(
        self,
        key: str,
        value: str,
        language: str = "",
        old_filename: str = "",
        new_filename: str = "",
    ) -> None:
        self._rust.put_diff(key, value, language, old_filename, new_filename)

    # ── Symbol-index cache ────────────────────────────────────────────────

    def get_symbol_index(self, cache_key: str) -> tuple[str, str] | None:
        return self._rust.get_symbol_index(cache_key)

    def put_symbol_index(
        self, cache_key: str, symbols_json: str, refs_json: str, file_count: int = 0
    ) -> None:
        self._rust.put_symbol_index(cache_key, symbols_json, refs_json, file_count)

    # ── Hover-map cache ───────────────────────────────────────────────────

    def get_hover_map(self, key: str) -> dict[str, str] | None:
        raw = self._rust.get_hover_map(key)
        return json.loads(raw) if raw is not None else None

    def put_hover_map(self, key: str, value: dict[str, str]) -> None:
        self._rust.put_hover_map(key, json.dumps(value))

    # ── Administrative ────────────────────────────────────────────────────

    def stats(self) -> dict:
        return json.loads(self._rust.stats())

    def metrics(self) -> dict:
        return json.loads(self._rust.metrics())

    def list_entries(
        self,
        table: str,
        *,
        language: str | None = None,
        file_glob: str | None = None,
        since: int | None = None,
        before: int | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Metadata rows (no BLOBs) with optional filters. The ``file_glob`` filter
        is applied here (fnmatch) over an over-fetched result set, matching the
        retired Python behaviour and keeping the glob off the SQL surface."""
        # Validate before crossing the FFI boundary so a bad limit is a ValueError
        # (matching the retired Python), not a pyo3 TypeError on the i64 argument.
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        raw = self._rust.list_entries(
            table, language, since, before, min_size, max_size, limit, file_glob is not None
        )
        entries = json.loads(raw)
        if file_glob and table == "diff_cache":
            import fnmatch  # noqa: PLC0415

            filtered: list[dict] = []
            for entry in entries:
                if fnmatch.fnmatch(
                    entry.get("old_filename", ""), file_glob
                ) or fnmatch.fnmatch(entry.get("new_filename", ""), file_glob):
                    filtered.append(entry)
                if len(filtered) >= limit:
                    break
            return filtered
        return entries[:limit]

    def get_entry_metadata(self, key: str, table: str) -> dict | None:
        raw = self._rust.get_entry_metadata(key, table)
        return json.loads(raw) if raw is not None else None

    def get_entry_payload(self, key: str, table: str) -> str | None:
        return self._rust.get_entry_payload(key, table)

    def export_entries(self, table: str):
        """Yield ``{table, key, ...metadata, payload}`` dicts for every entry."""
        yield from json.loads(self._rust.export_entries(table))

    def clear(
        self,
        parse: bool = True,
        diff: bool = True,
        index: bool = True,
        hover: bool = True,
    ) -> None:
        self._rust.clear(parse, diff, index, hover)

    def close(self) -> None:
        self._rust.close()
