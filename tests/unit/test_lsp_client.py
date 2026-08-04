"""Unit tests for :mod:`intentumdiff.lsp.client`.

We spin up a minimal asyncio TCP server that speaks JSON-RPC 2.0 with
Content-Length framing, so no real LSP server is required.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from intentumdiff.lsp.client import AsyncLspClient, _encode
from intentumdiff.lsp.config import LspServerConfig
from intentumdiff.lsp.exceptions import LspConnectionError, LspTimeoutError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro: Any) -> Any:
    """Run a coroutine synchronously (no pytest-asyncio required)."""
    return asyncio.run(coro)


def _response(req_id: int, result: Any) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


class FakeLspServer:
    """A TCP server that answers LSP requests with scripted replies.

    ``replies`` maps method name → result value to send back.
    If a method is absent the server reads the request but never replies
    (useful for testing timeouts).
    """

    def __init__(self, replies: dict[str, Any]) -> None:
        self._replies = replies
        self._server: asyncio.Server | None = None
        self.host = "127.0.0.1"
        self.port = 0

    async def __aenter__(self) -> "FakeLspServer":
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    raw = await reader.readuntil(b"\r\n")
                    line = raw.decode("ascii").rstrip()
                    if not line:
                        break
                    if ":" in line:
                        k, _, v = line.partition(":")
                        headers[k.strip().lower()] = v.strip()

                length = int(headers.get("content-length", 0))
                if length <= 0:
                    continue

                body = await reader.readexactly(length)
                msg = json.loads(body.decode("utf-8"))
                method = msg.get("method", "")
                req_id = msg.get("id")

                if req_id is None:
                    # notification — no response needed
                    continue

                if method not in self._replies:
                    # no entry → don't reply (timeout scenario)
                    continue

                writer.write(_response(req_id, self._replies[method]))
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


_INIT_REPLY: dict[str, Any] = {"capabilities": {"hoverProvider": True}}


# ---------------------------------------------------------------------------
# Tests: LspConnectionError on refused connection
# ---------------------------------------------------------------------------

def test_connection_refused() -> None:
    async def _run() -> None:
        cfg = LspServerConfig(host="127.0.0.1", port=1)
        client = AsyncLspClient(cfg, timeout=1.0)
        with pytest.raises(LspConnectionError):
            await client.start()

    run(_run())


# ---------------------------------------------------------------------------
# Tests: hover results
# ---------------------------------------------------------------------------

def test_hover_plain_string() -> None:
    async def _run() -> None:
        replies = {
            "initialize": _INIT_REPLY,
            "textDocument/hover": {"contents": "str"},
        }
        async with FakeLspServer(replies) as srv:
            cfg = LspServerConfig(host=srv.host, port=srv.port)
            async with AsyncLspClient(cfg, timeout=3.0) as client:
                result = await client.hover("file:///test.py", 0, 0)
        assert result == "str"

    run(_run())


def test_hover_markup_value() -> None:
    async def _run() -> None:
        replies = {
            "initialize": _INIT_REPLY,
            "textDocument/hover": {"contents": {"kind": "plaintext", "value": "int"}},
        }
        async with FakeLspServer(replies) as srv:
            cfg = LspServerConfig(host=srv.host, port=srv.port)
            async with AsyncLspClient(cfg, timeout=3.0) as client:
                result = await client.hover("file:///test.py", 5, 2)
        assert result == "int"

    run(_run())


def test_hover_list_contents() -> None:
    async def _run() -> None:
        replies = {
            "initialize": _INIT_REPLY,
            "textDocument/hover": {
                "contents": [{"kind": "plaintext", "value": "list[int]"}]
            },
        }
        async with FakeLspServer(replies) as srv:
            cfg = LspServerConfig(host=srv.host, port=srv.port)
            async with AsyncLspClient(cfg, timeout=3.0) as client:
                result = await client.hover("file:///test.py", 3, 1)
        assert result == "list[int]"

    run(_run())


def test_hover_null_result() -> None:
    """Server replies with null result → None returned."""
    async def _run() -> None:
        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                while True:
                    headers: dict[str, str] = {}
                    while True:
                        raw = await reader.readuntil(b"\r\n")
                        line = raw.decode("ascii").rstrip()
                        if not line:
                            break
                        if ":" in line:
                            k, _, v = line.partition(":")
                            headers[k.strip().lower()] = v.strip()
                    length = int(headers.get("content-length", 0))
                    body = await reader.readexactly(length)
                    msg = json.loads(body)
                    req_id = msg.get("id")
                    if req_id is None:
                        continue
                    writer.write(_response(req_id, None))
                    await writer.drain()
            except (asyncio.IncompleteReadError, asyncio.CancelledError):
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            cfg = LspServerConfig(host="127.0.0.1", port=port)
            async with AsyncLspClient(cfg, timeout=3.0) as client:
                result = await client.hover("file:///test.py", 0, 0)
            assert result is None
        finally:
            server.close()
            await server.wait_closed()

    run(_run())


def test_hover_timeout() -> None:
    """Server does not reply to hover → LspTimeoutError raised."""
    async def _run() -> None:
        replies = {
            "initialize": _INIT_REPLY,
            # No hover entry → FakeLspServer silently drops the request
        }
        async with FakeLspServer(replies) as srv:
            cfg = LspServerConfig(host=srv.host, port=srv.port)
            async with AsyncLspClient(cfg, timeout=0.2) as client:
                with pytest.raises(LspTimeoutError):
                    await client.hover("file:///test.py", 0, 0)

    run(_run())


def test_multiple_concurrent_hovers() -> None:
    """Multiple hover requests resolve independently."""
    async def _run() -> None:
        replies = {
            "initialize": _INIT_REPLY,
            "textDocument/hover": {"contents": {"kind": "plaintext", "value": "int"}},
        }
        async with FakeLspServer(replies) as srv:
            cfg = LspServerConfig(host=srv.host, port=srv.port)
            async with AsyncLspClient(cfg, timeout=3.0) as client:
                results = await asyncio.gather(
                    client.hover("file:///test.py", 0, 0),
                    client.hover("file:///test.py", 1, 0),
                    client.hover("file:///test.py", 2, 0),
                )
        assert all(r == "int" for r in results)

    run(_run())
