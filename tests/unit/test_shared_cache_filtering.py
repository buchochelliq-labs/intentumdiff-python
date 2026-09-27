"""Source-expected cache queries, shared with the native Rust public API."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest
from intentumdiff.cache.sqlite_store import SqliteCacheStore


def test_glob_finds_match_beyond_old_overfetch_window(tmp_path):
    path = tmp_path / "cache.db"
    with SqliteCacheStore(path) as store:
        store.put_diff("match", "{}", language="python", old_filename="old.py", new_filename="new.py")
        for i in range(11):
            store.put_diff(f"other-{i}", "{}", language="text", old_filename="a.txt", new_filename="b.txt")
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE diff_cache SET created_at=200")
            conn.execute("UPDATE diff_cache SET created_at=100 WHERE key='match'")
        assert [row["key"] for row in store.list_entries("diff_cache", file_glob="*.py", limit=1)] == ["match"]

CORPUS = json.loads((Path(__file__).parents[1] / "fixtures/cache_filtering.json").read_text())

@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["name"])
def test_source_expected_queries_and_native_parity(tmp_path, case):
    path = tmp_path / "corpus.db"
    with SqliteCacheStore(path) as store:
        for row in CORPUS["rows"]:
            store.put_diff(row["key"], "{}", language=row["language"], old_filename=row["old_filename"], new_filename=row["new_filename"])
        with sqlite3.connect(path) as conn:
            for row in CORPUS["rows"]:
                conn.execute("UPDATE diff_cache SET created_at=?, size_bytes=? WHERE key=?", (row["created_at"], row["size_bytes"], row["key"]))
        actual = store.list_entries("diff_cache", **case["query"])
        assert [row["key"] for row in actual] == case["expected_keys"]
        # Metadata only: no compressed payload should cross either public API.
        assert all("result" not in row for row in actual)
        probe = os.environ.get("INTENTUMDIFF_NATIVE_PROBE")
        if probe:
            native = json.loads(subprocess.check_output([probe], input=json.dumps({"handler":"cache_list_entries_filtered", "path":str(path), "query":case["query"]}), text=True))
            # These two fields depend on the wall clock of each sequential call.
            def stable(rows):
                return [{key:value for key,value in row.items() if key not in {"age_seconds", "expires_in_seconds"}} for row in rows]
            assert stable(native) == stable(actual)


def test_non_diff_pattern_remains_ignored_and_bool_limit_rejected(tmp_path):
    with SqliteCacheStore(tmp_path / "parse.db") as store:
        store.put_parse("one", "{}")
        assert [row["key"] for row in store.list_entries("parse_cache", file_glob="no-match")] == ["one"]
        with pytest.raises(ValueError, match="positive integer"):
            store.list_entries("parse_cache", limit=True)
