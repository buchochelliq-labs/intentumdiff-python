"""
intentumdiff.lsp.client
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``AsyncLspClient`` — asyncio TCP client for Language Server Protocol 3.17.

Protocol
--------
LSP uses JSON-RPC 2.0 framed with HTTP-style headers over a raw TCP socket::

    Content-Length: <n>\\r\\n
    \\r\\n
    <n bytes of UTF-8 JSON>

Requests carry a monotonically increasing integer ``id``.  The reader task
maps incoming response ``id`` values to ``asyncio.Future`` objects so that
concurrent ``await hover(...)`` calls all resolve independently.

Notifications (no ``id`` field) and server-initiated requests are silently
discarded — we only care about responses to our own requests.

Lifecycle
---------
1. ``await client.start()``         — TCP connect + ``initialize`` handshake
2. ``await client.did_open(...)``   — register a document
3. ``await client.hover(...)``      — query type information
4. ``await client.did_close(...)``  — unregister a document
5. ``await client.shutdown()``      — ``shutdown`` + ``exit`` notifications

Context manager usage::

    async with AsyncLspClient(config, timeout=5.0) as client:
        await client.did_open(uri, "python", source)
        info = await client.hover(uri, line=10, col=4)
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import shutil
import sys
from contextlib import suppress
from typing import Any

from intentumdiff.lsp.config import LspServerConfig
from intentumdiff.lsp.exceptions import LspConnectionError, LspTimeoutError

logger = logging.getLogger(__name__)

_ENCODING = "utf-8"
# Caps for the LSP reader to prevent memory exhaustion.
_MAX_HEADER_LINE = 4_096       # bytes per header line
_MAX_HEADERS_TOTAL = 16_384    # bytes of total header section
_MAX_BODY_BYTES = 67_108_864   # 64 MiB per message body
# Max bytes retained in the stderr ring buffer.
_STDERR_RING_BYTES = 65_536    # 64 KiB


def _subprocess_env() -> dict[str, str]:
    """Return os.environ with the current venv's Scripts/bin prepended to PATH.

    Ensures tools installed in the active venv (e.g. ``pylsp``, ``pyright``)
    are found when spawning subprocesses, even when the venv was not activated
    at the shell level before ``intentumdiff`` was launched.
    """
    env = dict(os.environ)
    scripts = os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")
    current = env.get("PATH", "")
    if scripts not in current.split(os.pathsep):
        env["PATH"] = scripts + os.pathsep + current
    return env


def _resolve_command(cmd: tuple[str, ...], env: dict[str, str]) -> tuple[str, ...]:
    """Resolve *cmd[0]* via :func:`shutil.which` using *env*'s PATH.

    On Windows, ``CreateProcess`` cannot find ``.cmd`` / ``.bat`` entry-point
    wrappers directly — they must be run through ``cmd.exe /c``.
    """
    resolved = shutil.which(cmd[0], path=env.get("PATH"))
    if resolved is None:
        return cmd  # let create_subprocess_exec raise a clear FileNotFoundError
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ("cmd.exe", "/c", resolved) + cmd[1:]
    return (resolved,) + cmd[1:]


def _encode(obj: dict[str, Any]) -> bytes:
    """Encode a JSON-RPC message with LSP Content-Length framing."""
    body = json.dumps(obj, ensure_ascii=False).encode(_ENCODING)
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _request(method: str, params: Any, req_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


def _notification(method: str, params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


class AsyncLspClient:
    """Asyncio TCP client for an already-running LSP server.

    Parameters
    ----------
    config:
        Connection parameters (host + port).
    timeout:
        Per-request timeout in seconds.  Exceeded requests raise
        ``LspTimeoutError`` (non-fatal — caller should degrade gracefully).
    root_uri:
        Workspace root passed to the ``initialize`` handshake.
        Defaults to ``"file:///tmp"`` which is accepted by all tested servers.
    """

    def __init__(
        self,
        config: LspServerConfig,
        timeout: float = 5.0,
        root_uri: str = "file:///tmp",
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._root_uri = root_uri

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._started = False
        # Subprocess handle — only set when transport is stdio.
        self._process: asyncio.subprocess.Process | None = None
        # Background task that drains the subprocess stderr pipe.
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buf: bytearray = bytearray()
        # Ring-buffer for stderr: holds at most _STDERR_RING_BYTES.
        # Using a deque of bytes chunks so we can cap total size without
        # copying on every append.
        self._stderr_ring: collections.deque[bytes] = collections.deque()
        self._stderr_ring_size: int = 0

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "AsyncLspClient":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.shutdown()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to the server and complete the ``initialize`` handshake."""
        if self._started:
            return
        try:
            if self._config.transport == "stdio":
                await self._start_stdio()
            else:
                await self._start_tcp()

            self._reader_task = asyncio.create_task(
                self._read_loop(), name=f"lsp-reader-{self._config}"
            )
            await self._initialize()
        except LspConnectionError:
            await self._cleanup_start_failure()
            raise
        except asyncio.CancelledError as exc:
            # The server closed its connection during the initialize handshake.
            # Yield once so _drain_stderr can read any buffered output.
            await asyncio.sleep(0)
            hint = self._stderr_hint()
            await self._cleanup_start_failure()
            raise LspConnectionError(
                f"LSP server {self._config} closed the connection "
                f"during the initialize handshake — the server may have crashed{hint}"
            ) from exc
        except Exception as exc:
            hint = self._stderr_hint()
            await self._cleanup_start_failure()
            raise LspConnectionError(
                f"Unexpected error while starting LSP client for {self._config}: {exc}{hint}"
            ) from exc
        self._started = True
        logger.debug("LSP client connected to %s", self._config)

    async def _cleanup_start_failure(self) -> None:
        """Cancel reader task and release resources after a failed :meth:`start`."""
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with suppress(Exception):
                await self._stderr_task
            self._stderr_task = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(Exception):
                await self._reader_task
            self._reader_task = None
        if self._writer is not None:
            with suppress(Exception):
                self._writer.close()
        if self._process is not None:
            with suppress(Exception):
                self._process.terminate()
            self._process = None

    async def _start_tcp(self) -> None:
        """Establish a TCP connection to an already-running server."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._config.host, self._config.port),
                timeout=self._timeout,
            )
        except (ConnectionRefusedError, OSError) as exc:
            raise LspConnectionError(
                f"Cannot connect to LSP server at {self._config} — "
                f"is the server running? ({exc})"
            ) from exc
        except asyncio.TimeoutError as exc:
            raise LspConnectionError(
                f"Timed out connecting to LSP server at {self._config}"
            ) from exc

    async def _start_stdio(self) -> None:
        """Spawn the server subprocess and attach to its stdin/stdout."""
        env = _subprocess_env()
        cmd = _resolve_command(self._config.command, env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (FileNotFoundError, OSError) as exc:
            raise LspConnectionError(
                f"Cannot start LSP server {self._config.command[0]!r}: {exc}"
            ) from exc
        self._process = proc
        self._reader = proc.stdout
        self._writer = proc.stdin  # type: ignore[assignment]
        # Drain stderr in the background so it never blocks the pipe.
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"lsp-stderr-{self._config}"
        )

    async def shutdown(self) -> None:
        """Send ``shutdown`` + ``exit`` and close the connection."""
        if not self._started:
            return
        with suppress(Exception):
            await self._send_request("shutdown", None)
            self._send_notification("exit", None)
        try:
            # Cancel background tasks first so they don't outlive the process.
            if self._stderr_task is not None:
                self._stderr_task.cancel()
                with suppress(BaseException):
                    await self._stderr_task
                self._stderr_task = None
            if self._reader_task:
                self._reader_task.cancel()
                with suppress(BaseException):
                    await self._reader_task
                self._reader_task = None
            # For stdio mode: terminate the process first, then skip calling
            # writer.close().  After process exit asyncio fires
            # pipe_connection_lost(stdin, BrokenPipeError) which sets
            # _stdin_closed; calling writer.close() afterwards would try to
            # set_result() on that already-done future → InvalidStateError.
            # For TCP mode (no subprocess): close the writer explicitly.
            if self._process is not None:
                try:
                    self._process.terminate()
                    await asyncio.wait_for(self._process.wait(), timeout=3.0)
                except (ProcessLookupError, asyncio.TimeoutError):
                    with suppress(Exception):
                        self._process.kill()
                        await asyncio.wait_for(self._process.wait(), timeout=1.0)
                except Exception:
                    logger.debug("Error while terminating LSP process", exc_info=True)
                finally:
                    self._process = None
            else:
                # TCP mode: no subprocess, close the writer transport directly.
                if self._writer:
                    self._writer.close()
                    with suppress(Exception):
                        await asyncio.wait_for(self._writer.wait_closed(), timeout=3.0)
            self._started = False
            logger.debug("LSP client disconnected from %s", self._config)
        finally:
            self._started = False

    # ── LSP methods ───────────────────────────────────────────────────────────

    async def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Notify the server that a document has been opened."""
        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    async def did_close(self, uri: str) -> None:
        """Notify the server that a document has been closed."""
        self._send_notification(
            "textDocument/didClose",
            {"textDocument": {"uri": uri}},
        )

    async def hover(self, uri: str, line: int, col: int) -> str | None:
        """Request hover information for a position.

        Returns the plain-text type string extracted from the hover response,
        or ``None`` if the server has no information at that position.

        Raises
        ------
        LspTimeoutError
            If the server does not respond within ``self._timeout`` seconds.
        """
        result = await self._send_request(
            "textDocument/hover",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": col},
            },
        )
        if result is None:
            return None
        # result is a Hover object: {"contents": ..., "range": ...}
        contents = result.get("contents")
        if not contents:
            return None
        if isinstance(contents, str):
            return contents.strip() or None
        if isinstance(contents, dict):
            value = contents.get("value", "")
            return value.strip() or None
        if isinstance(contents, list):
            texts = [
                (c.get("value", "") if isinstance(c, dict) else str(c))
                for c in contents
            ]
            combined = "\n".join(t for t in texts if t).strip()
            return combined or None
        return None

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _initialize(self) -> None:
        """Perform the LSP initialize / initialized handshake."""
        import os

        init_params: dict[str, Any] = {
            "processId": os.getpid(),
            "rootUri": self._root_uri,
            "workspaceFolders": [{"uri": self._root_uri, "name": "workspace"}],
            "capabilities": {
                "textDocument": {
                    "hover": {
                        "contentFormat": ["plaintext", "markdown"],
                    }
                },
                "workspace": {
                    "workspaceFolders": True,
                },
            },
            "clientInfo": {"name": "IntentumDiff", "version": "0.1"},
        }
        await self._send_request("initialize", init_params)
        self._send_notification("initialized", {})

    def _next_request_id(self) -> int:
        rid = self._next_id
        self._next_id += 1
        return rid

    def _send_raw(self, obj: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None:
            raise LspConnectionError(f"LSP client for {self._config} is not connected")
        writer.write(_encode(obj))
        # write is buffered; drain is called lazily for performance

    def _send_notification(self, method: str, params: Any) -> None:
        self._send_raw(_notification(method, params))

    async def _send_request(self, method: str, params: Any) -> Any:
        rid = self._next_request_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[rid] = fut
        self._send_raw(_request(method, params, rid))
        # Drain the write buffer
        writer = self._writer
        if writer is None:
            self._pending.pop(rid, None)
            raise LspConnectionError(f"LSP client for {self._config} is not connected")
        try:
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(rid, None)
            raise LspTimeoutError(
                f"Write timeout sending {method!r} to {self._config}"
            ) from exc
        try:
            return await asyncio.wait_for(
                asyncio.shield(fut), timeout=self._timeout
            )
        except asyncio.TimeoutError as exc:
            self._pending.pop(rid, None)
            raise LspTimeoutError(
                f"No response to {method!r} from {self._config} within {self._timeout}s"
            ) from exc

    async def _drain_stderr(self) -> None:
        """Background task: drain subprocess stderr into a capped ring buffer."""
        if self._process is None or self._process.stderr is None:
            return
        try:
            while chunk := await self._process.stderr.read(512):
                self._stderr_ring.append(chunk)
                self._stderr_ring_size += len(chunk)
                # Evict oldest chunks when over the limit.
                while self._stderr_ring_size > _STDERR_RING_BYTES and self._stderr_ring:
                    dropped = self._stderr_ring.popleft()
                    self._stderr_ring_size -= len(dropped)
        except Exception:
            logger.debug("Error while draining LSP stderr for %s", self._config, exc_info=True)

    def _stderr_hint(self) -> str:
        """Return a formatted hint string from captured stderr (empty if none)."""
        if not self._stderr_ring:
            return ""
        text = b"".join(self._stderr_ring).decode("utf-8", errors="replace").strip()
        if len(text) > 500:
            text = "\u2026" + text[-500:]
        return f" (stderr: {text})"

    async def _read_loop(self) -> None:
        """Background task: read framed JSON-RPC messages and dispatch them."""
        reader = self._reader
        if reader is None:
            logger.debug("LSP read loop started before reader was connected")
            return
        try:
            while True:
                # Read headers with size caps.
                headers: dict[str, str] = {}
                total_header_bytes = 0
                while True:
                    try:
                        raw_line = await reader.readuntil(b"\r\n")
                    except asyncio.LimitOverrunError:
                        # Header line exceeded the asyncio StreamReader limit.
                        logger.warning(
                            "LSP: header line too long from %s — closing.", self._config
                        )
                        return
                    if len(raw_line) > _MAX_HEADER_LINE:
                        logger.warning(
                            "LSP: header line exceeds %d bytes — closing.", _MAX_HEADER_LINE
                        )
                        return
                    total_header_bytes += len(raw_line)
                    if total_header_bytes > _MAX_HEADERS_TOTAL:
                        logger.warning(
                            "LSP: total headers exceed %d bytes — closing.", _MAX_HEADERS_TOTAL
                        )
                        return
                    line = raw_line.decode("ascii", errors="replace").rstrip()
                    if not line:
                        break  # blank line ends headers
                    if ":" in line:
                        k, _, v = line.partition(":")
                        headers[k.strip().lower()] = v.strip()

                length = int(headers.get("content-length", 0))
                if length <= 0:
                    continue
                if length > _MAX_BODY_BYTES:
                    logger.warning(
                        "LSP: Content-Length %d exceeds %d byte limit — closing.",
                        length,
                        _MAX_BODY_BYTES,
                    )
                    return

                body_bytes = await reader.readexactly(length)
                try:
                    msg: dict[str, Any] = json.loads(body_bytes.decode(_ENCODING))
                except json.JSONDecodeError:
                    logger.warning("LSP: malformed JSON frame from %s", self._config)
                    continue

                msg_id = msg.get("id")
                if msg_id is None:
                    # Server notification — ignore
                    continue

                fut = self._pending.pop(msg_id, None)
                if fut is None or fut.done():
                    continue

                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(
                        RuntimeError(
                            f"LSP error {err.get('code')}: {err.get('message')}"
                        )
                    )
                else:
                    fut.set_result(msg.get("result"))

        except asyncio.IncompleteReadError:
            logger.debug("LSP: server at %s closed connection", self._config)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("LSP read loop error (%s): %s", self._config, exc)
        finally:
            # Cancel all outstanding futures
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()
