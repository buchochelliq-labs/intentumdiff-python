"""
intentumdiff.core.cst_serializer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Converts a tree-sitter ``Node`` tree into the canonical JSON string consumed
by parser plugins (``interpret-cst`` mode).

Schema (each node object)
─────────────────────────
{
  "type":       "<node_type>",        // tree-sitter node.type
  "named":      true | false,         // tree-sitter node.is_named
  "text":       "<text>",             // only for leaf nodes
  "start_line": 0,                    // 0-based
  "start_col":  0,
  "end_line":   0,
  "end_col":    0,
  "children":   [ ... ]               // recursive; omitted for leaves
}
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # tree_sitter.Node is only imported at runtime inside functions so that
    # this module can be imported without tree-sitter being installed in tests
    # that mock out the CST serializer.
    from tree_sitter import Node

_MAX_TEXT_BYTES = 4096  # truncate leaf text beyond this to avoid huge payloads


def _node_to_dict(node: "Node", source_bytes: bytes) -> dict[str, Any]:
    """Recursively convert a tree-sitter Node to a plain dict.

    Only *named* nodes are included — anonymous punctuation tokens (``:``,
    ``(``, ``)``, keyword literals, etc.) are omitted.  This keeps payloads
    small: a typical Python file drops from ~3 000 nodes to ~2 000 nodes and
    reduces the JSON size by 30-50 %.  No semantic parser in the plugin
    ecosystem uses unnamed nodes; the generic parser already discards unnamed
    leaf nodes on its own side.
    """
    start = node.start_point  # (row, col) — 0-based
    end = node.end_point

    obj: dict[str, Any] = {
        "type": node.type,
        "named": node.is_named,
        "start_line": start[0],
        "start_col": start[1],
        "end_line": end[0],
        "end_col": end[1],
    }

    named_children = node.named_children  # list[Node] — unnamed nodes excluded
    if named_children:
        obj["children"] = [_node_to_dict(child, source_bytes) for child in named_children]
    else:
        # Leaf, or a node whose only children are unnamed punctuation.
        # Include the raw source text so plugins can read identifiers / literals.
        raw = source_bytes[node.start_byte : node.end_byte]
        if len(raw) > _MAX_TEXT_BYTES:
            logger.debug(
                "Leaf text truncated: %d bytes → %d for node type %r",
                len(raw), _MAX_TEXT_BYTES, node.type,
            )
        text = raw[:_MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        obj["text"] = text

    return obj


def serialize_cst(root: "Node", source_bytes: bytes) -> str:
    """
    Serialize a tree-sitter parse tree to a compact JSON string.

    Parameters
    ----------
    root:
        The root ``Node`` returned by ``tree_sitter.Parser.parse()``.
    source_bytes:
        The exact ``bytes`` object that was passed to the parser.

    Returns
    -------
    str
        A UTF-8 JSON string.  Separators are compact (no extra spaces).
    """
    d = _node_to_dict(root, source_bytes)
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False)


def deserialize_cst(cst_json: str) -> dict[str, Any]:
    """Parse a CST JSON string back into a plain dict (for tests / inspection)."""
    return json.loads(cst_json)  # type: ignore[no-any-return]
