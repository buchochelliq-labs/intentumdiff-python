"""
intentdiff.lsp.config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``LspServerConfig`` — connection parameters for one language's LSP server.

Two transport modes are supported, similar to MCP:

**TCP** (``transport="tcp"``)
    Connect to an already-running server over a TCP socket.

**Stdio** (``transport="stdio"``)
    Spawn the server as a subprocess and communicate via its stdin/stdout.
    The :class:`~intentdiff.lsp.client.AsyncLspClient` manages the
    subprocess lifecycle when this transport is used.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LspServerConfig(BaseModel, frozen=True):
    """Connection parameters for one language's LSP server.

    Use the factory classmethods :meth:`tcp` and :meth:`stdio` for
    construction, or :meth:`from_string` to parse ``"host:port"`` shorthand.

    TCP example::

        LspServerConfig.tcp("localhost", port=2087)
        LspServerConfig.from_string("localhost:2087")

    Stdio example::

        LspServerConfig.stdio("pylsp")
        LspServerConfig.stdio("typescript-language-server", "--stdio")
    """

    transport: Literal["tcp", "stdio"] = "tcp"

    # TCP fields
    host: str = Field(default="localhost", min_length=1)
    port: int | None = None

    # Stdio fields
    command: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "LspServerConfig":
        if self.transport == "tcp":
            if self.port is None:
                raise ValueError("port is required for tcp transport")
            if not (1 <= self.port <= 65535):
                raise ValueError("port must be between 1 and 65535")
        else:  # stdio
            if not self.command:
                raise ValueError("command must not be empty for stdio transport")
        return self

    # ── Factory classmethods ──────────────────────────────────────────────────

    @classmethod
    def tcp(cls, host: str = "localhost", *, port: int) -> "LspServerConfig":
        """Create a TCP transport config."""
        return cls(transport="tcp", host=host, port=port)

    @classmethod
    def stdio(cls, *command: str) -> "LspServerConfig":
        """Create a stdio transport config from a command and arguments."""
        return cls(transport="stdio", command=command)

    @classmethod
    def from_string(cls, value: str) -> "LspServerConfig":
        """Parse ``"host:port"`` shorthand into a TCP config.

        Examples::

            LspServerConfig.from_string("localhost:2087")
            LspServerConfig.from_string("127.0.0.1:9000")
        """
        m = re.fullmatch(r"([^:]+):(\d+)", value.strip())
        if not m:
            raise ValueError(
                f"Invalid LSP server address {value!r}. "
                "Expected 'host:port', e.g. 'localhost:2087'."
            )
        return cls(transport="tcp", host=m.group(1), port=int(m.group(2)))

    def __str__(self) -> str:
        if self.transport == "tcp":
            return f"{self.host}:{self.port}"
        return " ".join(self.command)
