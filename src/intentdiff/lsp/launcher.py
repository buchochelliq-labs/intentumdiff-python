"""
intentdiff.lsp.launcher
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``LspServerProcess`` — auto-start a language server and return a ready
:class:`~intentdiff.lsp.config.LspServerConfig`.

Two strategies mirror the transport modes:

**TCP servers** — manual already-running servers may still be used by passing
a TCP :class:`~intentdiff.lsp.config.LspServerConfig` directly to the
client.  Auto-started TCP subprocesses are legacy-only and require
``allow_unverified_tcp_autostart=True`` on the server spec because generic LSP
servers cannot prove they own the loopback port after reservation release.

**Stdio servers** — no external process is spawned here.  The launcher
returns a stdio config whose ``command`` the
:class:`~intentdiff.lsp.client.AsyncLspClient` will execute and manage
when its own ``start()`` is called.

Typical usage::

    from intentdiff.lsp.launcher import LspServerProcess
    from intentdiff.lsp.client import AsyncLspClient

    async with LspServerProcess("python") as cfg:
        async with AsyncLspClient(cfg) as client:
            enricher = TypeEnricher(client, "python")
            type_map = await enricher.enrich(path, source, root)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import sys
from contextlib import suppress

from intentdiff.lsp.config import LspServerConfig
from intentdiff.lsp.exceptions import LspConnectionError
from intentdiff.lsp.servers import KNOWN_SERVER_SPECS, LspServerSpec

logger = logging.getLogger(__name__)


def _subprocess_env() -> dict[str, str]:
    """Return os.environ with the current venv's Scripts/bin prepended to PATH."""
    env = dict(os.environ)
    scripts = os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")
    current = env.get("PATH", "")
    if scripts not in current.split(os.pathsep):
        env["PATH"] = scripts + os.pathsep + current
    return env


def _resolve_command(cmd: tuple[str, ...], env: dict[str, str]) -> tuple[str, ...]:
    """Resolve *cmd[0]* via shutil.which; on Windows wrap .cmd/.bat via cmd.exe."""
    resolved = shutil.which(cmd[0], path=env.get("PATH"))
    if resolved is None:
        return cmd
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ("cmd.exe", "/c", resolved) + cmd[1:]
    return (resolved,) + cmd[1:]


# ---------------------------------------------------------------------------
# Port utilities
# ---------------------------------------------------------------------------

def _reserve_port(host: str = "127.0.0.1") -> tuple[int, socket.socket]:
    """Bind to port 0, return (port, reservation_socket).

    Keep the socket open until the subprocess has been spawned.  On Windows,
    request exclusive-address ownership so another local process cannot co-bind
    the reserved port with SO_REUSEADDR while the reservation is held.

    Caller **must** call ``reservation_sock.close()`` after
    ``create_subprocess_exec`` returns.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind((host, 0))
    port: int = s.getsockname()[1]
    return port, s


async def _wait_for_port(host: str, port: int, *, timeout: float) -> None:
    """Poll until *host:port* accepts connections or *timeout* elapses.

    Raises :exc:`~intentdiff.lsp.exceptions.LspConnectionError` if the
    server does not become reachable within the timeout.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            return
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            if loop.time() >= deadline:
                raise LspConnectionError(
                    f"LSP server did not become reachable on {host}:{port} "
                    f"within {timeout:.0f}s"
                )
            await asyncio.sleep(0.25)


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

class LspServerProcess:
    """Manage the lifecycle of a spawned LSP server subprocess.

    Parameters
    ----------
    language:
        Language identifier (e.g. ``"python"``, ``"rust"``).
    spec:
        Optional :class:`~intentdiff.lsp.servers.LspServerSpec` to use
        instead of the built-in :data:`KNOWN_SERVER_SPECS` entry.  Pass this
        when loading a user-defined server from ``lsp_servers.json``.
    host:
        Loopback address to use for TCP servers (default ``"127.0.0.1"``).

    Usage::

        async with LspServerProcess("rust") as cfg:
            async with AsyncLspClient(cfg) as client:
                ...
    """

    def __init__(
        self,
        language: str,
        spec: "LspServerSpec | None" = None,
        host: str = "127.0.0.1",
    ) -> None:
        self._language = language
        self._spec = spec  # None → look up KNOWN_SERVER_SPECS at start()
        self._host = host
        # Set after start() for TCP servers; None for stdio servers.
        self._proc: asyncio.subprocess.Process | None = None
        self._config: LspServerConfig | None = None

    @property
    def config(self) -> LspServerConfig:
        """The connection config — available after :meth:`start` returns."""
        if self._config is None:
            raise RuntimeError(
                "LspServerProcess.start() has not been called yet."
            )
        return self._config

    async def start(self) -> LspServerConfig:
        """Spawn (TCP) or prepare (stdio) the server and return its config.

        Raises
        ------
        ValueError
            If *language* has no entry in :data:`KNOWN_SERVER_SPECS`.
        LspConnectionError
            If the server binary is not found or does not start in time.
        """
        spec = self._spec or KNOWN_SERVER_SPECS.get(self._language)
        if spec is None:
            available = ", ".join(sorted(KNOWN_SERVER_SPECS))
            raise ValueError(
                f"No auto-start spec for language {self._language!r}. "
                f"Available: {available}"
            )

        if spec.transport == "tcp":
            if not spec.allow_unverified_tcp_autostart:
                raise LspConnectionError(
                    "Auto-started TCP LSP servers are disabled by default. "
                    "Use a stdio server spec or connect to an already-running "
                    "manual TCP endpoint. Set allow_unverified_tcp_autostart "
                    "only for trusted legacy commands that accept the local "
                    "port-race residual risk."
                )
            logger.warning(
                "Starting %s LSP with unverified TCP auto-start. Prefer stdio "
                "or manual TCP because child port ownership cannot be proven.",
                self._language,
            )
            port, _reservation_sock = _reserve_port(self._host)
            cmd = tuple(
                part.format(host=self._host, port=port)
                for part in spec.command
            )
            env = _subprocess_env()
            resolved_cmd = _resolve_command(cmd, env)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *resolved_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except (FileNotFoundError, OSError) as exc:
                hint = f"\nInstall: {spec.install_hint}" if spec.install_hint else ""
                raise LspConnectionError(
                    f"Cannot start {self._language} LSP server "
                    f"({cmd[0]!r} not found).{hint}"
                ) from exc
            finally:
                # Release the reservation socket now that the subprocess has
                # been launched.  The reservation was exclusive on Windows, so
                # local co-binding is blocked while we hold it.
                _reservation_sock.close()

            try:
                await _wait_for_port(
                    self._host, port, timeout=spec.startup_timeout
                )
            except LspConnectionError:
                await self.stop()  # clean up the zombie process
                raise

            self._config = LspServerConfig.tcp(self._host, port=port)
            logger.debug(
                "Started %s LSP server (tcp) on %s:%d  pid=%d",
                self._language, self._host, port, self._proc.pid,
            )

        else:  # stdio
            cmd = tuple(
                part.format(pid=str(os.getpid()))
                for part in spec.command
            )
            self._config = LspServerConfig.stdio(*cmd)
            logger.debug(
                "Prepared %s LSP server (stdio): %s",
                self._language, " ".join(cmd),
            )

        return self._config

    async def stop(self) -> None:
        """Terminate the server subprocess.

        For **stdio** servers this is a no-op: the
        :class:`~intentdiff.lsp.client.AsyncLspClient` owns and
        terminates the process via :meth:`~AsyncLspClient.shutdown`.
        """
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except ProcessLookupError:
            pass  # already gone
        except (asyncio.TimeoutError, Exception):
            with suppress(Exception):
                self._proc.kill()
        finally:
            self._proc = None

    async def __aenter__(self) -> LspServerConfig:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
