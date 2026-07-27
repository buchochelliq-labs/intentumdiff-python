"""
intentdiff.cache.duckdb_store
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Append-only diff-history + fuel-diagnostics analytics store — a thin delegator over
the native Rust ``AnalyticsStore`` (#101, A2.2).

The schema, fuel-normalization, queries, and the read-only-query guard all live in the
shared Rust core (``crates/rust-core-host/src/analytics.rs``), so every binding records
and queries analytics identically. The storage engine is chosen at runtime by the core:
the **provided** DuckDB (dlopen of a configurable ``libduckdb``) when available, else the
bundled **SQLite** fallback — so analytics always works, DuckDB is never bundled, and the
core never fails to load without it.

Note: the on-disk file is engine-specific (a DuckDB file vs a SQLite file); switching
engines between runs can't read the other's prior data. Fine for append-only telemetry.
"""

from __future__ import annotations

import json
from pathlib import Path

from intentdiff.rust_core import analytics_store


def _check_limit(limit: int) -> None:
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")


class DuckDBAnalyticsStore:
    """Delegator over the native analytics store (class name kept for API compatibility)."""

    def __init__(self, path: Path | str) -> None:
        self._rust = analytics_store(str(path))

    @property
    def backend(self) -> str:
        """The active storage engine: ``"duckdb"`` or ``"sqlite"``."""
        return self._rust.backend

    # ── Write ─────────────────────────────────────────────────────────────

    def record_diff(self, diff_json: str) -> None:
        self._rust.record_diff(diff_json)

    def record_diagnostics_run(
        self,
        diffs: list[str | dict],
        *,
        command: str = "",
        repo: str = "",
        argv: list[str] | None = None,
        run_id: str | None = None,
    ) -> str:
        diffs_json = [d if isinstance(d, str) else json.dumps(d) for d in diffs]
        return self._rust.record_diagnostics_run(
            diffs_json, command, repo, json.dumps(argv or []), run_id
        )

    # ── Read ──────────────────────────────────────────────────────────────

    def query(self, sql: str) -> list[dict]:
        return json.loads(self._rust.query(sql))

    def query_readonly(self, sql: str) -> list[dict]:
        return json.loads(self._rust.query_readonly(sql))

    def most_changed_files(self, limit: int = 20) -> list[dict]:
        _check_limit(limit)
        return json.loads(self._rust.most_changed_files(limit))

    def changes_by_language(self) -> list[dict]:
        return json.loads(self._rust.changes_by_language())

    def recent_diagnostic_runs(self, limit: int = 10) -> list[dict]:
        _check_limit(limit)
        return json.loads(self._rust.recent_diagnostic_runs(limit))

    def fuel_by_language(self, limit: int = 20) -> list[dict]:
        _check_limit(limit)
        return json.loads(self._rust.fuel_by_language(limit))

    def top_fuel_hotspots(self, limit: int = 20) -> list[dict]:
        _check_limit(limit)
        return json.loads(self._rust.top_fuel_hotspots(limit))

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self._rust.close()

    def __enter__(self) -> "DuckDBAnalyticsStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
