"""#313: a failed or cancelled LSP start, and a failed send, leave nothing behind.

Uses real, bounded child "servers" (short Python scripts over stdio) as well as stubs.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from intentumdiff.lsp.client import AsyncLspClient, LspConnectionError
from intentumdiff.lsp.config import LspServerConfig

# Reads the initialize request header, then closes stdout and exits: a peer close.
_CLOSES = "import sys; sys.stdin.buffer.readline(); sys.stderr.write('boom\\n')"
# Never answers: start() only ends when the caller cancels it (or the timeout passes).
_SILENT = "import time; time.sleep(30)"


def _client(script: str, timeout: float = 10.0) -> AsyncLspClient:
    return AsyncLspClient(LspServerConfig.stdio(sys.executable, "-c", script), timeout=timeout)


def _assert_released(client: AsyncLspClient, process) -> None:
    assert client._stderr_task is None and client._reader_task is None
    assert client._process is None
    assert client._pending == {}
    assert process.returncode is not None, "child process was not reaped"
    assert client._writer is None or client._writer.transport.is_closing()


def test_peer_close_during_initialize_releases_everything() -> None:
    async def scenario() -> None:
        client = _client(_CLOSES)
        spawned = []
        original = client._start_stdio

        async def record():
            await original()
            spawned.append(client._process)

        client._start_stdio = record
        with pytest.raises(LspConnectionError, match="closed the connection"):
            await client.start()
        _assert_released(client, spawned[0])

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_caller_cancellation_is_preserved_and_releases_everything() -> None:
    async def scenario() -> None:
        client = _client(_SILENT)
        spawned = []
        original = client._start_stdio

        async def record():
            await original()
            spawned.append(client._process)

        client._start_stdio = record
        task = asyncio.create_task(client.start())
        while not spawned:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # now waiting for the initialize reply
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task  # external cancellation is not turned into LspConnectionError
        _assert_released(client, spawned[0])

    asyncio.run(asyncio.wait_for(scenario(), 20))


class _BrokenWriter:
    def __init__(self, *, write_error: Exception | None = None, drain_error=None) -> None:
        self.write_error, self.drain_error = write_error, drain_error

    def write(self, _data: bytes) -> None:
        if self.write_error:
            raise self.write_error

    async def drain(self) -> None:
        if self.drain_error:
            raise self.drain_error


@pytest.mark.parametrize(
    "writer",
    [
        None,
        _BrokenWriter(write_error=ConnectionResetError("peer gone")),
        _BrokenWriter(drain_error=BrokenPipeError("peer gone")),
    ],
    ids=["disconnected", "write-fails", "drain-fails"],
)
def test_failed_send_leaves_no_pending_request(writer) -> None:
    async def scenario() -> None:
        client = AsyncLspClient(LspServerConfig.tcp(port=1), timeout=1.0)
        client._writer = writer
        with pytest.raises((LspConnectionError, OSError)):
            await client._send_request("textDocument/hover", {})
        assert client._pending == {}

    asyncio.run(scenario())
