"""0.0.2 regression: stale caches and LSP errors never certify a partial index.

The public Indexer retains its host I/O, progress and DTO responsibilities;
Rust remains the only authority for parser and tree semantics.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from intentumdiff.core.indexer import Indexer, IndexResult
from intentumdiff.lsp.enricher import TypeEnricher
from intentumdiff.lsp.exceptions import LspConnectionError, LspTimeoutError


def _candidate(*, errors: list[tuple[str, str]] | None = None) -> IndexResult:
    return IndexResult(
        files_indexed=0 if errors else 1,
        files_skipped=0,
        errors=errors or [],
        semantic_index=MagicMock(),
    )


def test_plain_index_rejects_legacy_stale_symbol_index_cache() -> None:
    differ = MagicMock()
    differ._cache.get_symbol_index.return_value = ("{}", "{}")
    indexer = Indexer(differ)
    # The real parse failure must remain visible despite a historical "good"
    # index entry for the same repository path and Git commit.
    parsed = _candidate(errors=[("edit.py", "native_parse_incomplete")])
    with (
        patch("intentumdiff.vcs.git_cli.resolve_repo_root", return_value="/checkout"),
        patch("intentumdiff.core.indexer.load_diffignore", return_value=None),
        patch("intentumdiff.core.indexer._collect_blob_files", return_value=([("edit.py", "broken")], [])),
        patch.object(indexer, "_index_files", return_value=parsed) as run_index,
    ):
        result = indexer.index_repo("/checkout")

    assert result is parsed
    assert result.errors == [("edit.py", "native_parse_incomplete")]
    assert not result.from_cache
    run_index.assert_called_once()
    differ._cache.get_symbol_index.assert_not_called()
    differ._cache.put_symbol_index.assert_not_called()


def test_lsp_index_rejects_legacy_stale_symbol_index_cache() -> None:
    differ = MagicMock()
    differ._cache.get_symbol_index.return_value = ("{}", "{}")
    indexer = Indexer(differ)
    parsed = _candidate(errors=[("edit.py", "LSP transport failed")])
    with (
        patch("intentumdiff.vcs.git_cli.resolve_repo_root", return_value="/checkout"),
        patch("intentumdiff.core.indexer.load_diffignore", return_value=None),
        patch("intentumdiff.core.indexer._collect_blob_files", return_value=([("edit.py", "code")], [])),
        patch.object(indexer, "_index_files_lsp", new_callable=AsyncMock, return_value=parsed) as run_index,
    ):
        result = asyncio.run(indexer.index_repo_lsp("/checkout", lsp_clients={"python": object()}))

    assert result is parsed
    assert result.errors == [("edit.py", "LSP transport failed")]
    assert not result.from_cache
    run_index.assert_awaited_once()
    differ._cache.get_symbol_index.assert_not_called()
    differ._cache.put_symbol_index.assert_not_called()


def test_stale_hover_map_is_never_replayed_and_lsp_failure_is_reported() -> None:
    differ = MagicMock()
    differ.parse.return_value = (MagicMock(), "python")
    differ._cache.get_hover_map.return_value = {"stale-node": "old-type"}
    indexer = Indexer(differ)
    with (
        patch("intentumdiff.core.index.SemanticIndex") as sem_index,
        patch.object(TypeEnricher, "enrich", new_callable=AsyncMock, side_effect=RuntimeError("hover disconnected")) as hover,
    ):
        result = asyncio.run(
            indexer._index_files_lsp(
                [("file.py", "x = 1")],
                lsp_clients={"python": object()},
                on_progress=None,
            )
        )

    assert result.files_indexed == 0
    assert result.errors == [("file.py", "hover disconnected")]
    assert result.type_enriched_count == 0
    sem_index.return_value.add_tree.assert_not_called()
    hover.assert_awaited_once()
    differ._cache.get_hover_map.assert_not_called()
    differ._cache.put_hover_map.assert_not_called()


def test_genuine_empty_hover_map_is_not_an_enrichment_error() -> None:
    differ = MagicMock()
    differ.parse.return_value = (MagicMock(), "python")
    indexer = Indexer(differ)
    with (
        patch("intentumdiff.core.index.SemanticIndex") as sem_index,
        patch.object(TypeEnricher, "enrich", new_callable=AsyncMock, return_value={}) as hover,
    ):
        result = asyncio.run(
            indexer._index_files_lsp(
                [("file.py", "x = 1")],
                lsp_clients={"python": object()},
                on_progress=None,
            )
        )

    assert result.files_indexed == 1
    assert result.errors == []
    assert result.type_enriched_count == 0
    sem_index.return_value.add_tree.assert_called_once()
    hover.assert_awaited_once()


def test_lsp_did_open_connection_failure_is_not_empty_success() -> None:
    client = MagicMock()
    client.did_open = AsyncMock(side_effect=LspConnectionError("transport dropped"))
    client.did_close = AsyncMock()
    enricher = TypeEnricher(client, language="python")
    with pytest.raises(LspConnectionError, match="transport dropped"):
        asyncio.run(enricher.enrich("file.py", "x = 1", MagicMock()))
    client.did_close.assert_not_awaited()


def test_lsp_hover_timeout_is_not_silently_ignored() -> None:
    client = MagicMock()
    client.did_open = AsyncMock()
    client.did_close = AsyncMock()
    client.hover = AsyncMock(side_effect=LspTimeoutError("hover timed out"))
    enricher = TypeEnricher(client, language="python")
    with (
        patch.object(enricher, "_hover_triples", return_value=[("node-1", 0, 0)]),
        pytest.raises(RuntimeError, match="node-1.*hover timed out") as err,
    ):
        asyncio.run(enricher.enrich("file.py", "x = 1", MagicMock()))
    assert isinstance(err.value.__cause__, LspTimeoutError)
    client.did_close.assert_awaited_once()


def test_lsp_no_type_is_a_valid_completed_hover() -> None:
    client = MagicMock()
    client.did_open = AsyncMock()
    client.did_close = AsyncMock()
    client.hover = AsyncMock(return_value=None)
    enricher = TypeEnricher(client, language="python")
    with patch.object(enricher, "_hover_triples", return_value=[("node-1", 0, 0)]):
        result = asyncio.run(enricher.enrich("file.py", "x = 1", MagicMock()))
    assert result == {}
    client.did_close.assert_awaited_once()
