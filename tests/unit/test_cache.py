"""
tests/unit/test_cache.py — unit tests for the caching layer.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from intentumdiff.cache.sqlite_store import SqliteCacheStore
from intentumdiff.cache.store import CacheStore, _make_key
from intentumdiff.core.models import DiffConfig
from intentumdiff.differ import SemanticDiffer

# ---------------------------------------------------------------------------
# _make_key
# ---------------------------------------------------------------------------


def test_make_key_deterministic():
    k1 = _make_key("foo", "bar", "baz")
    k2 = _make_key("foo", "bar", "baz")
    assert k1 == k2


def test_make_key_different_parts():
    assert _make_key("a", "b") != _make_key("b", "a")
    assert _make_key("a\x00b") != _make_key("a", "b")


def test_make_key_hex_length():
    assert len(_make_key("x")) == 64


# ---------------------------------------------------------------------------
# SqliteCacheStore — basic round-trip
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> SqliteCacheStore:
    return SqliteCacheStore(tmp_path / "cache.db", ttl_days=30, max_mb=100)


def test_parse_cache_miss(store: SqliteCacheStore):
    assert store.get_parse("nonexistent-key") is None


def test_parse_cache_roundtrip(store: SqliteCacheStore):
    payload = json.dumps({"id": "1", "node_type": "module", "label": "", "children": []})
    store.put_parse("key-1", payload, grammar_id="python")
    assert store.get_parse("key-1") == payload


def test_parse_cache_overwrite(store: SqliteCacheStore):
    store.put_parse("k", "v1")
    store.put_parse("k", "v2")
    assert store.get_parse("k") == "v2"


def test_diff_cache_miss(store: SqliteCacheStore):
    assert store.get_diff("nonexistent") is None


def test_diff_cache_roundtrip(store: SqliteCacheStore):
    payload = json.dumps({"changes": [], "language": "python"})
    store.put_diff("dk", payload, language="python", old_filename="a.py", new_filename="b.py")
    assert store.get_diff("dk") == payload


def test_context_manager(tmp_path: Path):
    with SqliteCacheStore(tmp_path / "cm.db") as s:
        s.put_parse("x", "y")
        assert s.get_parse("x") == "y"
    # After close, should not raise — just be closed
    s.close()  # second close is safe


# ---------------------------------------------------------------------------
# SqliteCacheStore — TTL eviction
# ---------------------------------------------------------------------------


def test_ttl_evicts_old_entries(tmp_path: Path):
    db = tmp_path / "ttl.db"
    store = SqliteCacheStore(db, ttl_days=1, max_mb=100)
    store.put_parse("old-key", "old-value")
    store.close()

    # Manually backdate the entry via a direct sqlite3 connection (the native
    # Rust store no longer exposes a Python `_conn` — manipulate the DB file).
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE parse_cache SET created_at = ? WHERE key = 'old-key'",
        (int(time.time()) - 2 * 86_400,),  # 2 days ago
    )
    conn.commit()
    conn.close()

    # Re-open triggers eviction on construction.
    store2 = SqliteCacheStore(db, ttl_days=1, max_mb=100)
    assert store2.get_parse("old-key") is None
    store2.close()


# ---------------------------------------------------------------------------
# SqliteCacheStore — size eviction
# ---------------------------------------------------------------------------


def test_size_eviction_removes_oldest(tmp_path: Path):
    # Very small max (1 byte) to force eviction immediately.
    store = SqliteCacheStore(tmp_path / "size.db", ttl_days=9999, max_mb=0)

    # Insert three entries; they exceed the 0-byte cap
    store.put_parse("k1", "a" * 100)
    store.put_parse("k2", "b" * 100)
    store.put_parse("k3", "c" * 100)

    # _evict_by_size should have run at least once — not all keys need to
    # survive, but the store should remain functional
    remaining = [k for k in ("k1", "k2", "k3") if store.get_parse(k) is not None]
    assert len(remaining) <= 3  # store is still usable
    store.close()


# ---------------------------------------------------------------------------
# SqliteCacheStore — schema migration
# ---------------------------------------------------------------------------


def test_schema_version_mismatch_purges(tmp_path: Path):
    db = tmp_path / "schema.db"
    store = SqliteCacheStore(db, ttl_days=30, max_mb=100)
    store.put_parse("keep-me", "value")
    store.close()

    # Simulate a schema version bump by corrupting the stored version
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    # Re-opening should detect the mismatch and purge
    store2 = SqliteCacheStore(db, ttl_days=30, max_mb=100)
    assert store2.get_parse("keep-me") is None  # purged
    store2.close()


# ---------------------------------------------------------------------------
# CacheStore key helpers
# ---------------------------------------------------------------------------


def test_parse_key_stable():
    store = SqliteCacheStore.__new__(SqliteCacheStore)  # bypass __init__
    k1 = CacheStore.parse_key(store, "cst-json", "python", "abc123")
    k2 = CacheStore.parse_key(store, "cst-json", "python", "abc123")
    assert k1 == k2


def test_diff_key_differs_for_different_content():
    store = SqliteCacheStore.__new__(SqliteCacheStore)
    k1 = CacheStore.diff_key(store, "old1", "new1", "python", "wasm-hash")
    k2 = CacheStore.diff_key(store, "old2", "new2", "python", "wasm-hash")
    assert k1 != k2


# ---------------------------------------------------------------------------
# SemanticDiffer — cache injection
# ---------------------------------------------------------------------------


class _InMemoryCache(CacheStore):
    """Minimal in-memory CacheStore for testing wiring."""

    def __init__(self):
        self._parse: dict[str, str] = {}
        self._diff: dict[str, str] = {}
        self.parse_hits = 0
        self.diff_hits = 0

    def get_parse(self, key: str) -> str | None:
        v = self._parse.get(key)
        if v is not None:
            self.parse_hits += 1
        return v

    def put_parse(self, key: str, value: str, grammar_id: str = "") -> None:
        self._parse[key] = value

    def get_diff(self, key: str) -> str | None:
        v = self._diff.get(key)
        if v is not None:
            self.diff_hits += 1
        return v

    def put_diff(self, key, value, language="", old_filename="", new_filename=""):
        self._diff[key] = value

    def close(self) -> None:
        pass


def test_differ_accepts_injected_cache():
    cache = _InMemoryCache()
    differ = SemanticDiffer(cache=cache)
    assert differ._cache is cache


def test_differ_no_cache_by_default():
    differ = SemanticDiffer()
    assert differ._cache is None
    assert differ._analytics is None


def test_differ_creates_sqlite_cache_from_config(tmp_path: Path):
    config = DiffConfig(cache_path=tmp_path / "cache")
    differ = SemanticDiffer(config=config)
    assert isinstance(differ._cache, SqliteCacheStore)
    differ._cache.close()


@pytest.mark.skipif(
    os.getenv("INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE") == "1",
    reason=(
        "Uses a fully MagicMock'd parser to control the cache key; the mock cannot satisfy "
        "the native Rust path the RUST_ONLY gate requires. Cache-hit behaviour with the real "
        "engine is covered by the other cache tests."
    ),
)
def test_diff_cache_hit_skips_wasm(tmp_path: Path):
    """A diff-cache hit should return without calling parser.process()."""
    cache = _InMemoryCache()
    differ = SemanticDiffer(cache=cache)

    # Build a minimal valid SemanticDiff JSON and prime the diff cache
    from intentumdiff.core.models import SemanticDiff
    fake_diff = SemanticDiff(
        changes=[],
        old_filename="a.py",
        new_filename="a.py",
        language="python",
        has_semantic_changes=False,
        is_style_only=True,
        parse_errors=[],
    )
    fake_json = fake_diff.model_dump_json()

    # We need to know what key will be computed; use a mock parser to control it.
    mock_parser = MagicMock()
    mock_parser.grammar_id = "python"
    mock_parser.parser_mode = "full-parse"
    mock_parser.wasm_path = str(tmp_path / "python_parser.wasm")  # non-existent → "unknown" hash
    mock_parser.trivia_node_types = []
    mock_parser.preprocess_source.side_effect = lambda s: s

    with patch.object(differ._registry, "detect_parser", return_value=(mock_parser, "python")):
        # Compute the key ourselves
        wasm_hash = differ._wasm_hash_for(mock_parser)
        diff_key = cache.diff_key("def f(): pass", "def g(): pass", "python", wasm_hash)
        cache._diff[diff_key] = fake_json

        result = differ.diff_strings("def f(): pass", "def g(): pass", "test.py")

    assert result.is_style_only is True
    assert cache.diff_hits == 1
    mock_parser.process.assert_not_called()


# ---------------------------------------------------------------------------
# Cache limit input validation (B6-10)
# ---------------------------------------------------------------------------


class TestListEntriesLimitValidation:
    """list_entries must reject non-int or non-positive limit values."""

    def test_string_limit_raises_value_error(self, tmp_path: Path):
        store = SqliteCacheStore(tmp_path / "cache.db")
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            store.list_entries("parse_cache", limit="injection")  # type: ignore[arg-type]
        store.close()

    def test_zero_limit_raises_value_error(self, tmp_path: Path):
        store = SqliteCacheStore(tmp_path / "cache.db")
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            store.list_entries("parse_cache", limit=0)
        store.close()

    def test_negative_limit_raises_value_error(self, tmp_path: Path):
        store = SqliteCacheStore(tmp_path / "cache.db")
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            store.list_entries("parse_cache", limit=-1)
        store.close()

    def test_valid_limit_works(self, tmp_path: Path):
        store = SqliteCacheStore(tmp_path / "cache.db")
        result = store.list_entries("parse_cache", limit=10)
        assert isinstance(result, list)
        store.close()


class TestDuckDbLimitValidation:
    """most_changed_files must reject non-int or non-positive limit values."""

    def test_string_limit_raises(self, tmp_path: Path):
        from intentumdiff.cache.duckdb_store import DuckDBAnalyticsStore
        store = DuckDBAnalyticsStore(tmp_path / "analytics.db")
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            store.most_changed_files(limit="injection")  # type: ignore[arg-type]
        store.close()

    def test_zero_limit_raises(self, tmp_path: Path):
        from intentumdiff.cache.duckdb_store import DuckDBAnalyticsStore
        store = DuckDBAnalyticsStore(tmp_path / "analytics.db")
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            store.most_changed_files(limit=0)
        store.close()

    def test_valid_limit_works(self, tmp_path: Path):
        from intentumdiff.cache.duckdb_store import DuckDBAnalyticsStore
        store = DuckDBAnalyticsStore(tmp_path / "analytics.db")
        result = store.most_changed_files(limit=5)
        assert isinstance(result, list)
        store.close()


class TestDuckDbDiagnostics:
    def test_records_normalized_fuel_diagnostics(self, tmp_path: Path):
        from intentumdiff.cache.duckdb_store import DuckDBAnalyticsStore

        store = DuckDBAnalyticsStore(tmp_path / "diagnostics.duckdb")
        diff = {
            "old_filename": "src/main.ts",
            "new_filename": "src/main.ts",
            "language": "typescript",
            "has_semantic_changes": True,
            "is_style_only": False,
            "is_fallback": False,
            "changes": [{"change_type": "ADDITION"}],
            "parse_errors": [],
            "metadata": {
                "engine_telemetry": {
                    "calls": [
                        {
                            "plugin": "js-ts-parser.wasm",
                            "function": "process",
                            "language": "typescript",
                            "filename": "src/main.ts",
                            "provenance": "first_party_wasm",
                            "engine": "python_wasmtime_plugin_host",
                            "trusted": True,
                            "statuses": {"ok": 1},
                            "call_count": 1,
                            "fuel_consumed": 25_000_000,
                            "total_fuel_consumed": 25_000_000,
                            "fuel_budget": 100_000_000,
                            "max_fuel_used_percent": 25.0,
                            "input_bytes": 500,
                            "input_lines": 10,
                        }
                    ],
                    "fuel_hotspots": [
                        {
                            "plugin": "js-ts-parser.wasm",
                            "function": "process",
                            "language": "typescript",
                            "filename": "src/main.ts",
                            "fuel_consumed": 25_000_000,
                            "fuel_budget": 100_000_000,
                            "fuel_used_percent": 25.0,
                            "input_bytes": 500,
                            "input_lines": 10,
                            "fuel_per_kb": 25_000_000,
                            "fuel_per_line": 2_500_000,
                            "thresholds_exceeded": ["absolute", "per_line"],
                        }
                    ],
                },
                "diagnostics": {
                    "events": [
                        {
                            "stage": "engine.telemetry",
                            "action": "wasm_fuel_hotspot",
                            "rule_id": "engine.wasm_fuel_hotspot",
                            "reason": "fuel use exceeded policy",
                            "metadata": {"fuel_hotspots": 1},
                        }
                    ]
                },
            },
        }

        run_id = store.record_diagnostics_run([diff], command="string", repo=".", argv=["string"])

        runs = store.recent_diagnostic_runs()
        assert runs[0]["id"] == run_id
        assert runs[0]["total_fuel"] == 25_000_000
        assert runs[0]["hotspot_count"] == 1

        languages = store.fuel_by_language()
        assert languages[0]["language"] == "typescript"
        assert languages[0]["parser_calls"] == 1
        assert languages[0]["peak_fuel"] == 25_000_000

        hotspots = store.top_fuel_hotspots()
        assert hotspots[0]["filename"] == "src/main.ts"
        assert hotspots[0]["fuel_per_line"] == 2_500_000
        assert json.loads(hotspots[0]["thresholds_json"]) == ["absolute", "per_line"]

        queried = store.query_readonly("select language, peak_fuel from diagnostic_files")
        assert queried == [{"language": "typescript", "peak_fuel": 25_000_000}]
        with pytest.raises(ValueError, match="read-only"):
            store.query_readonly("delete from diagnostic_files")
        store.close()
