"""
intentumdiff.lsp.enricher
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``TypeEnricher`` — attaches LSP hover type information to ``SemanticNode`` leaves.

Usage
-----
::

    async with AsyncLspClient(config) as client:
        enricher = TypeEnricher(client, language="python")
        type_map = await enricher.enrich(uri, source_text, root_node)
        # type_map: dict[node_id, type_string]

The Rust engine selects hover targets and result node IDs. This adapter uses
``asyncio.gather`` to parallelise all hover requests for a single file,
which keeps latency proportional to the server's response time rather than
the number of nodes.

LSP failures are non-fatal:
- ``LspConnectionError`` — logged as a warning; an empty dict is returned.
- ``LspTimeoutError`` per node — that node is silently skipped.
- Any other exception — logged as a debug message; the node is skipped.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib

from intentumdiff.lsp.client import AsyncLspClient
from intentumdiff.lsp.exceptions import LspConnectionError, LspTimeoutError
from intentumdiff.core.models import SemanticNode

logger = logging.getLogger(__name__)

# Maximum concurrent hover requests per file.  Most servers handle far more,
# but we cap to avoid overwhelming a slow server.
_MAX_CONCURRENT = 50


def _path_to_uri(path: str) -> str:
    """Convert a filesystem path to a ``file://`` URI."""
    return pathlib.Path(path).resolve().as_uri()


class TypeEnricher:
    """Queries an LSP server for type information for all name-leaf nodes.

    Parameters
    ----------
    client:
        A connected ``AsyncLspClient``.
    language:
        LSP ``languageId`` for ``didOpen`` notifications (e.g. ``"python"``).
    """

    def __init__(self, client: AsyncLspClient, language: str) -> None:
        self._client = client
        self._language = language

    async def enrich(
        self,
        file_path: str,
        source_text: str,
        root: SemanticNode,
    ) -> dict[str, str]:
        """Return a ``{node_id: type_string}`` mapping for all name leaves.

        Sends ``textDocument/didOpen``, fires all hover requests concurrently,
        then sends ``textDocument/didClose``.

        Returns an empty dict on connection failure so that the caller can
        continue the diff pipeline without type information.
        """
        uri = _path_to_uri(file_path)
        try:
            await self._client.did_open(uri, self._language, source_text)
        except LspConnectionError as exc:
            logger.warning("LSP type enrichment skipped (%s): %s", file_path, exc)
            return {}
        try:
            result = await self._query_all(uri, root, source_text)
        finally:
            try:
                await self._client.did_close(uri)
            except Exception:
                logger.debug("LSP didClose failed for %s", uri, exc_info=True)
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _hover_triples(self, root: SemanticNode, source_text: str) -> list[tuple[str, int, int]]:
        """Return the Rust engine's target decisions; failures propagate."""
        from intentumdiff.rust_core import collect_lsp_hover_targets
        return collect_lsp_hover_targets(root, source_text)

    async def _query_node(
        self,
        uri: str,
        node_id: str,
        line: int,
        col: int,
        sem: asyncio.Semaphore,
    ) -> tuple[str, str | None]:
        """Query hover at ``line:col``; return (node_id, type_string|None)."""
        async with sem:
            try:
                type_str = await self._client.hover(uri, line, col)
                return node_id, type_str
            except LspTimeoutError:
                return node_id, None
            except Exception as exc:
                logger.debug(
                    "LSP hover failed for node %s at %d:%d — %s",
                    node_id, line, col, exc,
                )
                return node_id, None

    async def _query_all(
        self,
        uri: str,
        root: SemanticNode,
        source_text: str,
    ) -> dict[str, str]:
        """Fire all hover requests concurrently; collect non-None results."""
        targets = self._hover_triples(root, source_text)
        if not targets:
            return {}

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        tasks = [self._query_node(uri, nid, line, col, sem) for nid, line, col in targets]
        pairs = await asyncio.gather(*tasks)

        return {nid: t for nid, t in pairs if t is not None}
