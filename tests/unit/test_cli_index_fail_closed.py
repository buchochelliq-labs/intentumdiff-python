"""Release regression: CLI indexing cannot turn partial work into exit-zero success."""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from intentumdiff.cli import _commands


def _result(*, errors: list[tuple[str, str]]) -> SimpleNamespace:
    return SimpleNamespace(
        from_cache=False,
        files_indexed=0 if errors else 1,
        files_skipped=0,
        skipped_files=[],
        errors=errors,
        files_ignored=0,
        ignored_files=[],
        type_enriched_count=0,
    )


def test_index_cli_exits_nonzero_after_displaying_file_error() -> None:
    with (
        patch.object(_commands, "_console") as console,
        pytest.raises(SystemExit) as status,
    ):
        _commands._print_index_result(
            _result(errors=[("broken.py", "native_parse_incomplete")])
        )

    assert status.value.code == 1
    output = "\n".join(str(call) for call in console.print.call_args_list)
    assert "broken.py" in output
    assert "native_parse_incomplete" in output


def test_index_cli_allows_completed_index_with_no_errors() -> None:
    with patch.object(_commands, "_console"):
        _commands._print_index_result(_result(errors=[]))


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        repo="/checkout", ref="HEAD", force=False, trust_repo_lsp_config=False
    )


def test_explicit_lsp_fails_when_no_server_supports_detected_language() -> None:
    indexer = MagicMock()
    indexer.detect_languages.return_value = ({"unknown-language"}, 1)
    with (
        patch("rich.progress.Progress") as progress,
        patch("intentumdiff.lsp.servers.KNOWN_SERVER_SPECS", {}),
        patch.object(_commands, "_console"),
        patch.object(_commands, "_index_with_progress", new_callable=AsyncMock) as plain_index,
        pytest.raises(RuntimeError, match="no LSP servers are configured"),
    ):
        progress.return_value.__enter__.return_value = MagicMock()
        asyncio.run(_commands._two_pass_index(_args(), MagicMock(), indexer))

    plain_index.assert_not_awaited()


def test_explicit_lsp_fails_when_all_available_servers_are_declined() -> None:
    indexer = MagicMock()
    indexer.detect_languages.return_value = ({"testlang"}, 1)
    spec = SimpleNamespace(command=["test-lsp"])
    with (
        patch("rich.progress.Progress") as progress,
        patch("intentumdiff.lsp.servers.KNOWN_SERVER_SPECS", {"testlang": spec}),
        patch.object(_commands, "_console") as console,
        patch.object(_commands, "_index_with_progress", new_callable=AsyncMock) as plain_index,
        pytest.raises(RuntimeError, match="no configured LSP server connected"),
    ):
        progress.return_value.__enter__.return_value = MagicMock()
        console.input.return_value = "n"
        asyncio.run(_commands._two_pass_index(_args(), MagicMock(), indexer))

    plain_index.assert_not_awaited()
