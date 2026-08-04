"""
intentumdiff.cache
~~~~~~~~~~~~~~~~~~~~~~

Disk-persistent caching and analytics for the semantic diff pipeline.

Two stores are provided:

``SqliteCacheStore``
    Stores parse-tree and diff results keyed by content + grammar hash.
    Backed by SQLite with WAL mode; safe for concurrent readers.
    Evicts entries by TTL and total DB size.

``DuckDBAnalyticsStore``
    Append-only diff-history store backed by DuckDB.
    Exposes a ``query(sql)`` method for ad-hoc analytics.

Typical usage::

    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import DiffConfig
    from pathlib import Path

    config = DiffConfig(
        cache_path=Path(".intentumdiff-cache"),
        cache_ttl_days=30,
        cache_max_mb=500,
        analytics_path=Path(".intentumdiff-cache"),
    )
    differ = SemanticDiffer(config=config)
"""

from intentumdiff.cache.store import CacheStore
from intentumdiff.cache.sqlite_store import SqliteCacheStore
from intentumdiff.cache.duckdb_store import DuckDBAnalyticsStore

__all__ = ["CacheStore", "SqliteCacheStore", "DuckDBAnalyticsStore"]
