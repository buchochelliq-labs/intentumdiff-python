"""
intentdiff.lsp_server.server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Factory function that creates and wires a pygls ``LanguageServer`` for
``intentdiff lsp-server``.

Usage::

    from intentdiff.lsp_server import create_server

    server = create_server()
    server.start_io()          # stdio transport  (--stdio)
    # or
    server.start_tcp("127.0.0.1", 2087)   # TCP transport  (--tcp PORT)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lsprotocol import types as lsp_types
try:
    from pygls.lsp.server import LanguageServer  # pygls >= 2.x
except ImportError:
    from pygls.server import LanguageServer  # pygls 1.x

from intentdiff.core.models import DiffConfig
from intentdiff.differ import SemanticDiffer
from intentdiff.lsp_server._codelens import semantic_diff_to_codelens
from intentdiff.lsp_server._diagnostics import semantic_diff_to_diagnostics
from intentdiff.lsp_server._handlers import (
    _ServerState,
    _do_diff,
    schedule_diff,
    uri_to_path,
)

log = logging.getLogger(__name__)


def create_server(
    config: DiffConfig | None = None,
    ref: str = "HEAD",
    debounce: float = 0.15,
) -> LanguageServer:
    """Create a fully-wired ``LanguageServer`` instance.

    Parameters
    ----------
    config:
        ``DiffConfig`` to pass to ``SemanticDiffer``.  Defaults to
        ``DiffConfig()`` (all defaults).
    ref:
        Git ref to compare the live buffer against (default: ``"HEAD"``).
    debounce:
        Keystroke debounce delay in seconds (default: ``0.15``).

    Returns
    -------
    LanguageServer
        Ready to start via ``.start_io()`` or ``.start_tcp(host, port)``.
    """
    differ = SemanticDiffer(config or DiffConfig())
    state = _ServerState(differ=differ, ref=ref, debounce=debounce)

    server = LanguageServer(
        name="intentdiff-lsp",
        version="1.0.0",
        text_document_sync_kind=lsp_types.TextDocumentSyncKind.Full,
    )

    # ------------------------------------------------------------------
    # Capability check after initialization handshake
    # ------------------------------------------------------------------

    @server.feature(lsp_types.INITIALIZED)
    def _on_initialized(params: lsp_types.InitializedParams) -> None:
        """Read client capabilities to decide whether to send refresh requests."""
        try:
            # pygls stores client capabilities on server.workspace after init.
            client_caps = getattr(server.workspace, "client_capabilities", None)
            if client_caps is None:
                return
            ws_caps = getattr(client_caps, "workspace", None)
            if ws_caps is None:
                return
            cl_caps = getattr(ws_caps, "code_lens", None)
            state.supports_codelens_refresh = bool(
                getattr(cl_caps, "refresh_support", False)
            )
        except Exception:  # noqa: BLE001
            log.debug("Could not read LSP client capabilities", exc_info=True)

    # ------------------------------------------------------------------
    # Document lifecycle handlers (push model)
    # ------------------------------------------------------------------

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_OPEN)
    async def _did_open(params: lsp_types.DidOpenTextDocumentParams) -> None:
        uri = params.text_document.uri
        content = params.text_document.text
        await schedule_diff(server, state, uri, content)

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_CHANGE)
    async def _did_change(params: lsp_types.DidChangeTextDocumentParams) -> None:
        uri = params.text_document.uri
        if not params.content_changes:
            return
        # Full sync — the last (and only) item is always the complete document.
        content = params.content_changes[-1].text
        await schedule_diff(server, state, uri, content)

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_CLOSE)
    def _did_close(params: lsp_types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        state.diff_cache.pop(uri, None)
        task = state.pending_tasks.pop(uri, None)
        if task is not None and not task.done():
            task.cancel()

    # ------------------------------------------------------------------
    # Code-lens handler (pull model — returns from cache synchronously)
    # ------------------------------------------------------------------

    @server.feature(
        lsp_types.TEXT_DOCUMENT_CODE_LENS,
        lsp_types.CodeLensOptions(resolve_provider=False),
    )
    def _code_lens(params: lsp_types.CodeLensParams) -> list[lsp_types.CodeLens]:
        uri = params.text_document.uri
        diff = state.diff_cache.get(uri)
        if diff is None:
            return []
        return semantic_diff_to_codelens(diff)

    # ------------------------------------------------------------------
    # Custom request: intentdiff/semanticDiff
    # ------------------------------------------------------------------

    @server.feature("intentdiff/semanticDiff")
    def _semantic_diff(params: dict) -> dict:
        """Return a JSON-serialisable ``SemanticDiff`` for the requested URIs.

        Parameters (request params dict)
        ---------------------------------
        oldUri : str
            ``file://`` URI of the *old* version.
        newUri : str
            ``file://`` URI of the *new* version.

        If ``oldUri == newUri`` the workspace-tracked buffer content is diffed
        against the committed ``ref`` version.  Otherwise both files are read
        from disk and compared directly.
        """
        old_uri: str = params.get("oldUri", "")
        new_uri: str = params.get("newUri", "")
        if not old_uri or not new_uri:
            return {"error": "oldUri and newUri are required"}

        try:
            if old_uri == new_uri:
                # Same URI — use live workspace content vs. HEAD.
                doc = server.workspace.get_text_document(new_uri)
                content = doc.source
                diff = _do_diff(state, server, new_uri, content)
            else:
                # Different URIs — read both from disk and compare.
                from intentdiff.sources.string_source import StringSource

                # Derive workspace root for containment check.
                workspace_root: Path | None = None
                root_uri_str = getattr(getattr(server, "workspace", None), "root_uri", None)
                if root_uri_str:
                    try:
                        workspace_root = uri_to_path(root_uri_str)
                    except Exception:
                        workspace_root = None

                if workspace_root is None:
                    return {"error": "intentdiff/semanticDiff requires a valid workspace root"}

                old_path = uri_to_path(old_uri, workspace_root=workspace_root)
                new_path = uri_to_path(new_uri, workspace_root=workspace_root)
                old_content = old_path.read_text(encoding="utf-8", errors="replace")
                new_content = new_path.read_text(encoding="utf-8", errors="replace")
                source = StringSource(
                    old_content=old_content,
                    new_content=new_content,
                    filename=new_path.name,
                )
                diff = state.differ.diff(source)

            if diff is None:
                return {"error": "Diff computation failed"}

            return json.loads(diff.model_dump_json())

        except Exception as exc:  # noqa: BLE001
            log.warning("intentdiff/semanticDiff error: %s", exc)
            return {"error": str(exc)}

    return server
