"""Tree and fallback-diff helpers for intentumdiff.differ."""

from __future__ import annotations

import logging
from typing import Any

from intentumdiff.core.models import (
    SemanticDiff,
    SemanticNode,
)

logger = logging.getLogger(__name__)

def _validate_tree_ids(root: SemanticNode, context: str) -> None:
    """
    Verify that every node in the tree has a unique ID.

    Duplicate IDs within a single tree cause silent correctness bugs in the
    matching phase (the second node with a given ID is silently skipped).
    Raises ``ValueError`` when duplicates are detected, naming the offending
    IDs so plugin authors can diagnose the issue quickly.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for node in [root] + root.descendants():
        if node.id in seen:
            duplicates.add(node.id)
        seen.add(node.id)
    if duplicates:
        raise ValueError(
            f"Plugin produced duplicate node IDs in {context!r}: "
            f"{sorted(duplicates)}.  Each node must have a unique 'id' field."
        )


def _count_semantic_nodes(root: SemanticNode) -> int:
    return 1 + len(root.descendants())


def _empty_semantic_tree(language: str) -> SemanticNode:
    from intentumdiff.rust_core import _c_abi_call
    return SemanticNode.model_validate(_c_abi_call("empty_semantic_tree", language))


def _has_error_node(tree: SemanticNode) -> bool:
    """Ask Rust whether a semantic tree contains a parse error."""
    from intentumdiff.rust_core import parse_errors_present
    return parse_errors_present("", tree.model_dump_json(), "")


def _token_fallback_diff(
    old_content: str, new_content: str, old_filename: str, new_filename: str,
    language: str, *, metadata: dict[str, Any] | None = None,
) -> SemanticDiff:
    """Compatibility adapter for Rust's source-preserving fallback."""
    from intentumdiff.rust_core import source_fallback_diff
    details = metadata or {}
    result = source_fallback_diff(old_content, new_content, old_filename,
        new_filename, language, str(details.get("fallback_reason", "parse_errors")))
    return result.model_copy(update={"metadata": {**details, **dict(result.metadata)}})


def _enrich_literal_labels(root: SemanticNode, source: str) -> SemanticNode:
    """Delegate literal-label enrichment to Rust; never reconstruct semantics."""
    from intentumdiff.rust_core import enrich_literal_labels

    return enrich_literal_labels(root, source)
