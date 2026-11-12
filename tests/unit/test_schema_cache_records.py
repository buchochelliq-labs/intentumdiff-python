"""#307: corrupt optional schema-cache records are cache misses, never review failures."""

from __future__ import annotations

import hashlib
import json
import urllib.error
from pathlib import Path

import pytest

from intentumdiff.analysis.schema_resolver import (
    DEFAULT_SCHEMA_TTL_SECONDS,
    FetchResponse,
    _schema_ttl_seconds,
    resolve_schema,
)

_URL = "https://example.test/schema.json"
_CONTENT = json.dumps({"$schema": _URL, "tasks": []})
_NOW = 10_000.0
_SCHEMA = {"type": "object", "properties": {"task_key": {"type": "string"}}}

_GOOD = {"source_url": _URL, "fetched_at": _NOW - 1, "content_hash": "h", "schema": _SCHEMA}
_BAD_RECORDS = [
    pytest.param("{", id="truncated-json"),
    pytest.param("not json", id="not-json"),
    pytest.param(json.dumps([]), id="list-top-level"),
    pytest.param(json.dumps("x"), id="string-top-level"),
    pytest.param(json.dumps({"schema": {}}), id="missing-fetched_at"),
    *[
        pytest.param(json.dumps({**_GOOD, "fetched_at": value}), id=f"fetched_at-{name}")
        for name, value in [
            ("null", None), ("string", "1000"), ("list", [1]), ("bool", True),
            ("future", _NOW + 3600),
        ]
    ],
    pytest.param(json.dumps({**_GOOD, "fetched_at": "NaN"}).replace('"NaN"', "NaN"), id="nan"),
    pytest.param(
        json.dumps({**_GOOD, "fetched_at": "Inf"}).replace('"Inf"', "Infinity"), id="infinity"
    ),
    pytest.param(json.dumps({**_GOOD, "schema": "x"}), id="schema-not-object"),
]


def _env(**overrides: str) -> dict[str, str]:
    return {"INTENTUMDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS": "1", **overrides}


def _write_record(root: Path, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{hashlib.sha256(_URL.encode()).hexdigest()}.json").write_text(text, "utf-8")


def _resolve(root: Path, fetcher, **env: str):
    return resolve_schema(
        content=_CONTENT, filename="workflow.json", language="json",
        env=_env(**env), cache_dir=root, fetcher=fetcher, now=_NOW,
    )


def _failing(url: str, headers: dict[str, str]) -> FetchResponse:
    raise urllib.error.URLError("offline")


@pytest.mark.parametrize("record", _BAD_RECORDS)
def test_cache_only_treats_a_bad_record_as_a_miss(tmp_path: Path, record: str) -> None:
    _write_record(tmp_path, record)
    result = _resolve(tmp_path, _failing, INTENTUMDIFF_SCHEMA_FETCH="cache-only")
    assert result.status == "cache_miss"
    assert result.schema is None


@pytest.mark.parametrize("record", _BAD_RECORDS)
def test_fetch_failure_does_not_fall_back_to_a_bad_record(tmp_path: Path, record: str) -> None:
    _write_record(tmp_path, record)
    result = _resolve(tmp_path, _failing, INTENTUMDIFF_SCHEMA_FETCH="auto")
    assert result.status == "error"
    assert result.schema is None


@pytest.mark.parametrize("record", _BAD_RECORDS)
def test_successful_fetch_replaces_a_bad_record(tmp_path: Path, record: str) -> None:
    _write_record(tmp_path, record)

    def fetcher(url: str, headers: dict[str, str]) -> FetchResponse:
        assert "If-None-Match" not in headers  # a corrupt record supplies no validators
        return FetchResponse(body=json.dumps(_SCHEMA).encode(), final_url=url)

    assert _resolve(tmp_path, fetcher, INTENTUMDIFF_SCHEMA_FETCH="auto").status == "fetched"
    again = _resolve(tmp_path, _failing, INTENTUMDIFF_SCHEMA_FETCH="cache-only")
    assert again.status == "cache_hit"
    assert again.identity_fields == {"task_key"}
    assert [path.suffix for path in tmp_path.iterdir()] == [".json"]  # replaced atomically


def test_a_good_record_is_still_a_hit(tmp_path: Path) -> None:
    _write_record(tmp_path, json.dumps({**_GOOD, "etag": '"v1"'}))
    result = _resolve(tmp_path, _failing, INTENTUMDIFF_SCHEMA_FETCH="cache-only")
    assert result.status == "cache_hit"
    assert result.identity_fields == {"task_key"}


@pytest.mark.parametrize(
    ("raw", "ttl"),
    [
        ("inf", DEFAULT_SCHEMA_TTL_SECONDS), ("-inf", DEFAULT_SCHEMA_TTL_SECONDS),
        ("1e309", DEFAULT_SCHEMA_TTL_SECONDS), ("nan", DEFAULT_SCHEMA_TTL_SECONDS),
        ("abc", DEFAULT_SCHEMA_TTL_SECONDS), ("", DEFAULT_SCHEMA_TTL_SECONDS),
        ("-5", 0), ("60", 60), ("2.9", 2),
    ],
)
def test_ttl_is_finite(raw: str, ttl: int) -> None:
    assert _schema_ttl_seconds({"INTENTUMDIFF_SCHEMA_CACHE_TTL_SECONDS": raw}) == ttl
