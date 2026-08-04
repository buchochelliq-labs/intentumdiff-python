"""
intentumdiff.lsp
~~~~~~~~~~~~~~~~~~~~~

Optional LSP type-enrichment layer.

Connects to a Language Server Protocol server — either via **TCP** or
**stdio** — and queries ``textDocument/hover`` to attach type information
to ``SemanticNode`` leaves.  All exports are ``None``-safe: if no LSP server
is configured the enrichment step is a no-op and every other pipeline stage
is unaffected.

Auto-start usage::

    # CLI — detect languages and start servers automatically:
    intentumdiff index --lsp

    # Programmatic
    from intentumdiff.lsp.launcher import LspServerProcess
    from intentumdiff.lsp.client import AsyncLspClient

    async with LspServerProcess("python") as cfg:
        async with AsyncLspClient(cfg) as client:
            ...

Manual TCP usage::

    intentumdiff index --lsp-server python=localhost:2087

Public API
----------
- ``LspServerConfig``   — connection config (tcp or stdio)
- ``AsyncLspClient``    — asyncio JSON-RPC 2.0 client (both transports)
- ``TypeEnricher``      — queries hover and builds ``node_id → type_info`` maps
- ``LspServerProcess``  — auto-start a known language server subprocess
- ``LspServerSpec``     — server specification dataclass
- ``KNOWN_SERVER_SPECS``— auto-start specs for 15 languages
- ``KNOWN_SERVERS``     — legacy default host:port strings
- ``LspConnectionError``, ``LspTimeoutError`` — non-fatal warning exceptions
"""

from intentumdiff.lsp.client import AsyncLspClient
from intentumdiff.lsp.config import LspServerConfig
from intentumdiff.lsp.enricher import TypeEnricher
from intentumdiff.lsp.exceptions import LspConnectionError, LspTimeoutError
from intentumdiff.lsp.launcher import LspServerProcess
from intentumdiff.lsp.servers import (
    KNOWN_SERVERS,
    KNOWN_SERVER_SPECS,
    LspServerEntry,
    LspServerSpec,
    load_lsp_servers_json,
)

__all__ = [
    "LspServerConfig",
    "LspConnectionError",
    "LspTimeoutError",
    "AsyncLspClient",
    "TypeEnricher",
    "LspServerProcess",
    "LspServerSpec",
    "LspServerEntry",
    "KNOWN_SERVER_SPECS",
    "KNOWN_SERVERS",
    "load_lsp_servers_json",
]
