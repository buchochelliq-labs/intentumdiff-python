"""
intentdiff.lsp.enricher
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``TypeEnricher`` — attaches LSP hover type information to ``SemanticNode`` leaves.

Usage
-----
::

    async with AsyncLspClient(config) as client:
        enricher = TypeEnricher(client, language="python")
        type_map = await enricher.enrich(uri, source_text, root_node)
        # type_map: dict[node_id, type_string]

The enricher only queries leaf nodes whose ``node_type`` is in
``_NAME_TYPES`` (identifiers, names, symbols …).  It uses
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

from intentdiff.lsp.client import AsyncLspClient
from intentdiff.lsp.exceptions import LspConnectionError, LspTimeoutError
from intentdiff.core.models import SemanticNode

logger = logging.getLogger(__name__)

# Node types whose *own* position is hovered and whose *own* id gets the result.
# Kept narrow: only specific name nodes the refactoring rules sub-classify.
# Generic ``identifier``/``name``/``symbol`` are excluded — they appear
# hundreds of times per file but are never directly read for ``type_info`` by
# any refactoring rule.
_NAME_TYPES = frozenset(
    {
        "function_name",
        "class_name",
        "variable_name",
        "method_name",  # some parsers emit method_name instead of function_name
    }
)

# Declaration/parameter node types whose ``type_info`` is read by refactoring
# rules (EXTRACT_VARIABLE, INTRODUCE_PARAMETER_OBJECT).  The enricher hovers
# at the *first name-leaf child's* position but stores the result under the
# *declaration node's* id so it lands on the node the rules inspect.
_DECL_NODE_TYPES = frozenset(
    {
        "assignment",
        "variable_declaration",
        "let_declaration",
        "const_declaration",
        "local_variable_declaration",
        "variable_declarator",
    }
)
_PARAM_NODE_TYPES = frozenset(
    {
        "parameter",
        "typed_parameter",
        "typed_identifier",
        "required_parameter",
        "optional_parameter",
    }
)

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
            result = await self._query_all(uri, root)
        finally:
            try:
                await self._client.did_close(uri)
            except Exception:
                logger.debug("LSP didClose failed for %s", uri, exc_info=True)
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _first_name_leaf(self, node: SemanticNode) -> SemanticNode | None:
        """Return the first leaf descendant whose node_type is a name type."""
        all_name_types = _NAME_TYPES | frozenset({"identifier", "name", "symbol"})
        for child in node.children:
            if child.is_leaf() and child.node_type in all_name_types:
                return child
            found = self._first_name_leaf(child)
            if found is not None:
                return found
        return None

    def _collect_hover_targets(
        self, root: SemanticNode
    ) -> list[tuple[SemanticNode, SemanticNode]]:
        """Return ``(result_node, position_node)`` pairs to hover.

        * For **name-type leaves** (:data:`_NAME_TYPES`): hover at the leaf
          position, store result under the leaf's id.
        * For **declaration/parameter nodes** (:data:`_DECL_NODE_TYPES`,
          :data:`_PARAM_NODE_TYPES`): hover at the first name-leaf child's
          position, but store the result under the *declaration node's* id.
          This makes ``type_info`` land on the node that refactoring rules
          actually inspect (e.g. the ``assignment`` node for
          EXTRACT_VARIABLE).

        Result node ids are deduplicated so each node is queried at most once.
        """
        seen: set[str] = set()
        targets: list[tuple[SemanticNode, SemanticNode]] = []

        for node in [root, *root.descendants()]:
            if node.id in seen:
                continue
            if node.node_type in _NAME_TYPES and node.is_leaf():
                seen.add(node.id)
                targets.append((node, node))
            elif node.node_type in (_DECL_NODE_TYPES | _PARAM_NODE_TYPES):
                leaf = self._first_name_leaf(node)
                if leaf is not None:
                    seen.add(node.id)
                    targets.append((node, leaf))

        return targets

    def _hover_triples(self, root: SemanticNode) -> list[tuple[str, int, int]]:
        """``(result_node_id, line, col)`` hover targets — Rust core first (issue 100 S2),
        with :meth:`_collect_hover_targets` as the Python fallback/oracle."""
        from intentdiff.rust_core import try_rust_collect_hover_targets

        rust_triples = try_rust_collect_hover_targets(root)
        if rust_triples is not None:
            return rust_triples
        return [
            (rn.id, pn.position.start_line, pn.position.start_col)
            for rn, pn in self._collect_hover_targets(root)
        ]

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
    ) -> dict[str, str]:
        """Fire all hover requests concurrently; collect non-None results."""
        targets = self._hover_triples(root)
        if not targets:
            return {}

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        tasks = [self._query_node(uri, nid, line, col, sem) for nid, line, col in targets]
        pairs = await asyncio.gather(*tasks)

        return {nid: t for nid, t in pairs if t is not None}
