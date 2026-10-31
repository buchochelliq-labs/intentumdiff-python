"""0.0.2: an unsuccessful live Rust review must never preserve stale LSP results."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pygls")

from lsprotocol import types as lsp_types
from intentumdiff.core.models import SemanticDiff
from intentumdiff.lsp_server._handlers import _ServerState, _compute_and_push


def _diff() -> SemanticDiff:
    return SemanticDiff(old_filename="test.py", new_filename="test.py", language="python")


@pytest.mark.parametrize("mode", ["declined", "raised"])
def test_failed_live_review_invalidates_previous_cache_and_publishes_error(mode: str) -> None:
    uri = "file:///project/test.py"
    server = MagicMock()
    state = _ServerState(differ=MagicMock(), ref="HEAD", debounce=0,
                         supports_codelens_refresh=True)
    state.diff_cache[uri] = _diff()  # previous successful review

    outcome = ({"return_value": None} if mode == "declined"
               else {"side_effect": RuntimeError("incomplete Rust result")})
    with patch("intentumdiff.lsp_server._handlers._do_diff", **outcome):
        asyncio.run(_compute_and_push(server, state, uri, "def broken("))

    assert uri not in state.diff_cache, "stale semantics must be invalidated"
    server.publish_diagnostics.assert_called_once()
    actual_uri, diagnostics = server.publish_diagnostics.call_args.args
    assert actual_uri == uri
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == lsp_types.DiagnosticSeverity.Error
    assert "previous results are invalid" in diagnostics[0].message
    server.lsp.send_request.assert_called_once_with("workspace/codeLens/refresh", None)


def test_successful_followup_replaces_cache_and_clears_error_diagnostic() -> None:
    uri = "file:///project/test.py"
    server = MagicMock()
    state = _ServerState(differ=MagicMock(), ref="HEAD", debounce=0,
                         supports_codelens_refresh=True)
    state.diff_cache[uri] = _diff()
    fresh = _diff()
    with patch("intentumdiff.lsp_server._handlers._do_diff", return_value=fresh):
        asyncio.run(_compute_and_push(server, state, uri, "def repaired(): pass"))

    assert state.diff_cache[uri] is fresh
    server.publish_diagnostics.assert_called_once_with(uri, [])
    server.lsp.send_request.assert_called_once_with("workspace/codeLens/refresh", None)
