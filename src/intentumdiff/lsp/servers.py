"""
intentumdiff.lsp.servers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Known language server specifications for auto-start support.

Each entry in :data:`KNOWN_SERVER_SPECS` describes how to launch the server
for a given language.  Two transports are supported:

**tcp** — IntentumDiff connects to a loopback TCP socket.  Manual
already-running TCP servers are supported.  Auto-started TCP servers are
legacy-only and require an explicit trust opt-in because generic LSP servers
cannot prove post-bind child ownership.

**stdio** — the server is started with stdin/stdout pipes and the
:class:`~intentumdiff.lsp.client.AsyncLspClient` communicates directly
over those streams.  No TCP socket is involved.

The command templates may contain ``{host}`` and ``{port}`` placeholders
(resolved at launch time by :class:`~intentumdiff.lsp.launcher.LspServerProcess`).

See https://langserver.org/ for the full directory of language server implementations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass  # kept for future annotations


@dataclass(frozen=True)
class LspServerSpec:
    """Auto-start specification for a language server.

    Attributes
    ----------
    command:
        Executable and arguments.  For TCP servers, ``{host}`` and ``{port}``
        placeholders are substituted at launch time.
    transport:
        ``"tcp"`` — a separate process that listens on a TCP port.
        ``"stdio"`` — a process that speaks LSP over stdin/stdout.
    startup_timeout:
        Seconds to wait for a TCP server to become reachable after being
        spawned.  Ignored for stdio servers.
    install_hint:
        Human-readable install command shown when the server binary is
        not found.
    allow_unverified_tcp_autostart:
        Legacy escape hatch for TCP auto-start.  Leave ``False`` unless the
        operator explicitly trusts the command and accepts the local port-race
        residual risk.  Prefer stdio or manual already-running TCP instead.
    """

    command: tuple[str, ...]
    transport: Literal["tcp", "stdio"] = "stdio"
    startup_timeout: float = 15.0
    install_hint: str = ""
    allow_unverified_tcp_autostart: bool = False


# ---------------------------------------------------------------------------
# Auto-start specs
# ---------------------------------------------------------------------------

KNOWN_SERVER_SPECS: dict[str, LspServerSpec] = {
    # ── Stdio-preferred built-ins (intentumdiff owns the child pipes) ───────────────
    "python": LspServerSpec(
        command=("pylsp",),
        transport="stdio",
        startup_timeout=10.0,
        install_hint="uv pip install python-lsp-server",
    ),
    "go": LspServerSpec(
        command=("gopls",),
        transport="stdio",
        startup_timeout=20.0,
        install_hint="go install golang.org/x/tools/gopls@latest",
    ),
    "ruby": LspServerSpec(
        command=("solargraph", "stdio"),
        transport="stdio",
        startup_timeout=15.0,
        install_hint="gem install solargraph",
    ),
    "php": LspServerSpec(
        command=("phpactor", "language-server"),
        transport="stdio",
        startup_timeout=10.0,
        install_hint="composer global require phpactor/phpactor",
    ),

    # ── Stdio-native (spawn with pipes; client owns the process) ────────────
    "javascript": LspServerSpec(
        command=("typescript-language-server", "--stdio"),
        transport="stdio",
        install_hint="npm install -g typescript-language-server typescript",
    ),
    "typescript": LspServerSpec(
        command=("typescript-language-server", "--stdio"),
        transport="stdio",
        install_hint="npm install -g typescript-language-server typescript",
    ),
    "tsx": LspServerSpec(
        command=("typescript-language-server", "--stdio"),
        transport="stdio",
        install_hint="npm install -g typescript-language-server typescript",
    ),
    "rust": LspServerSpec(
        command=("rust-analyzer",),
        transport="stdio",
        install_hint="rustup component add rust-analyzer",
    ),
    "c": LspServerSpec(
        command=("clangd",),
        transport="stdio",
        install_hint="https://clangd.llvm.org/installation",
    ),
    "cpp": LspServerSpec(
        command=("clangd",),
        transport="stdio",
        install_hint="https://clangd.llvm.org/installation",
    ),
    "bash": LspServerSpec(
        command=("bash-language-server", "start"),
        transport="stdio",
        install_hint="npm install -g bash-language-server",
    ),
    "kotlin": LspServerSpec(
        command=("kotlin-language-server",),
        transport="stdio",
        install_hint="https://github.com/fwcd/kotlin-language-server/releases",
    ),
    "swift": LspServerSpec(
        command=("sourcekit-lsp",),
        transport="stdio",
        install_hint="Included with Xcode or https://swift.org/download",
    ),
    "java": LspServerSpec(
        command=("jdtls",),
        transport="stdio",
        install_hint="https://github.com/eclipse/eclipse.jdt.ls",
    ),
    "csharp": LspServerSpec(
        command=("OmniSharp", "--languageserver"),
        transport="stdio",
        install_hint="https://github.com/OmniSharp/omnisharp-roslyn/releases",
    ),

    # ── dbt Fusion LSP (dbt-sql, dbt-yaml) ─────────────────────────────────
    # dbt-lsp is the language server shipped alongside the dbt Fusion engine.
    # It understands dbt-flavoured SQL and YAML, providing type information
    # from compiled warehouse schemas.  Install via the dbt VS Code extension
    # (https://docs.getdbt.com/docs/install-dbt-extension) or directly:
    #   curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh \
    #     | sh -s -- --update --package dbt-lsp
    "dbt-sql": LspServerSpec(
        command=("dbt-lsp",),
        transport="stdio",
        install_hint="https://docs.getdbt.com/docs/install-dbt-extension",
    ),
    "dbt-yaml": LspServerSpec(
        command=("dbt-lsp",),
        transport="stdio",
        install_hint="https://docs.getdbt.com/docs/install-dbt-extension",
    ),
}

# ---------------------------------------------------------------------------
# Legacy compat: default host:port addresses for manual server connections
# ---------------------------------------------------------------------------

KNOWN_SERVERS: dict[str, str] = {
    "python":     "localhost:2087",
    "javascript": "localhost:2088",
    "typescript": "localhost:2088",
    "tsx":        "localhost:2088",
    "rust":       "localhost:2089",
    "java":       "localhost:2090",
    "go":         "localhost:2091",
    "csharp":     "localhost:2092",
    "c":          "localhost:2093",
    "cpp":        "localhost:2093",
    "ruby":       "localhost:2094",
    "swift":      "localhost:2095",
    "kotlin":     "localhost:2096",
    "php":        "localhost:2097",
    "bash":       "localhost:2098",
    "dbt-sql":    "localhost:2099",
    "dbt-yaml":   "localhost:2100",
}

# ---------------------------------------------------------------------------
# User-defined server config — loaded from lsp_servers.json
# ---------------------------------------------------------------------------

class LspServerEntry(BaseModel, frozen=True):
    """One user-defined LSP server entry, as it appears in ``lsp_servers.json``.

    Each entry in that file is keyed by an arbitrary server name (like MCP)
and **must** include a ``language`` field so IntentumDiff knows which
    language it serves.

    Example ``lsp_servers.json``::

        {
          "my-pylsp": {
            "language": "python",
            "transport": "stdio",
            "command": ["pylsp"]
          },
          "tsserver": {
            "language": "typescript",
            "transport": "stdio",
            "command": ["typescript-language-server", "--stdio"]
          },
          "running-gopls": {
            "language": "go",
            "transport": "tcp",
            "host": "localhost",
            "port": 2091
          }
        }

    When ``transport`` is ``"tcp"`` and ``command`` is provided the server can
    only be auto-started by :class:`~intentumdiff.lsp.launcher.LspServerProcess`
    when ``allow_unverified_tcp_autostart`` is set to ``true``.  This is a
    legacy trust decision; prefer ``stdio``.
    When ``transport`` is ``"tcp"`` and **no** command is given, the server is
treated as already-running and IntentumDiff simply connects to
    ``host:port``.
    """

    language: str = Field(min_length=1, description="Language identifier (e.g. 'python').")
    transport: Literal["tcp", "stdio"] = Field(default="stdio")
    command: tuple[str, ...] = Field(
        default=(),
        description="Command to spawn the server. For tcp, may include {host}/{port} placeholders.",
    )
    host: str = Field(default="localhost", min_length=1)
    port: int | None = Field(
        default=None,
        description="Static TCP port (manual-connect mode only).",
    )
    startup_timeout: float = Field(default=15.0, gt=0)
    install_hint: str = ""
    allow_unverified_tcp_autostart: bool = Field(
        default=False,
        description=(
            "Legacy opt-in for auto-started TCP LSP commands. Prefer stdio or "
            "manual TCP. When false, tcp+command entries fail closed before launch."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> "LspServerEntry":
        if self.transport == "stdio" and not self.command:
            raise ValueError("command is required for stdio transport")
        if self.transport == "tcp" and not self.command and self.port is None:
            raise ValueError(
                "For tcp without command (manual-connect mode) port is required"
            )
        if self.port is not None and not (1 <= self.port <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return self

    @property
    def is_manual_connect(self) -> bool:
        """``True`` when this is a pre-started server we just connect to.

        This is the case for ``transport="tcp"`` entries that have **no**
        ``command`` — just ``host`` and ``port``.
        """
        return self.transport == "tcp" and not self.command

    def to_spec(self) -> LspServerSpec:
        """Convert to an :class:`LspServerSpec` for auto-start use.

        Only valid for entries that have a ``command`` (auto-start mode).
        """
        if not self.command:
            raise ValueError(
                "Cannot convert a manual-connect entry (no command) to LspServerSpec. "
                "Use LspServerConfig.tcp(host, port=port) directly."
            )
        return LspServerSpec(
            command=self.command,
            transport=self.transport,
            startup_timeout=self.startup_timeout,
            install_hint=self.install_hint,
            allow_unverified_tcp_autostart=self.allow_unverified_tcp_autostart,
        )


def load_lsp_servers_json(path: "Path | str") -> dict[str, LspServerEntry]:
    """Load user-defined LSP server entries from an ``lsp_servers.json`` file.

    Returns a mapping of **language → entry**.  If multiple entries share the
    same language the last one in the file wins.  Returns an empty dict if the
    file does not exist.

    Parameters
    ----------
    path:
        Path to the ``lsp_servers.json`` file.

    Raises
    ------
    ValueError
        If the file is not valid JSON or any entry fails validation.
    """
    p = Path(path)
    if not p.exists():
        return {}

    with p.open("r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p}: invalid JSON — {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{p}: expected a JSON object at the top level")

    result: dict[str, LspServerEntry] = {}
    for server_name, data in raw.items():
        if not isinstance(data, dict):
            raise ValueError(
                f"{p}: entry {server_name!r} must be a JSON object"
            )
        try:
            entry = LspServerEntry.model_validate(data)
        except Exception as exc:
            raise ValueError(
                f"{p}: invalid entry {server_name!r}: {exc}"
            ) from exc
        # Key by language; first entry for a language wins.
        if entry.language not in result:
            result[entry.language] = entry
        else:
            logger.debug(
                "lsp_servers.json: skipping %r — language %r already registered",
                server_name, entry.language,
            )

    return result
