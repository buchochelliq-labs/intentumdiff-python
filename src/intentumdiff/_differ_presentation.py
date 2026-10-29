"""Tree, markdown-presentation, fallback-diff and stream helpers for intentumdiff.differ."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from intentumdiff.analysis.text_review import (
    PresentationResult,
)
from intentumdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeGroupKind,
    ChangeType,
    NodePosition,
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


def _root_structural_hash(node: SemanticNode) -> str:
    """Return the structural hash of a SemanticNode root (already computed)."""
    return node.structural_hash


def _node_to_dict(node: SemanticNode) -> dict[str, Any]:
    return json.loads(node.model_dump_json())


def _compute_structural_hash_for_tree(cst_json: str) -> str:
    """
    Compute the structural hash of the FILTERED (trivia-stripped) CST.
    Used for the style-only shortcut before running the full diff algorithm.
    """
    from intentumdiff.plugins.loader import _structural_hash_impl

    return _structural_hash_impl(cst_json)


def _count_cst_nodes(cst_json: str) -> int:
    """Count total nodes in a CST JSON string (recursive depth-first)."""

    def _count(node: Any) -> int:
        return 1 + sum(_count(c) for c in node.get("children", ()))

    try:
        return _count(json.loads(cst_json))
    except Exception:
        return 0


def _count_semantic_nodes(root: SemanticNode) -> int:
    return 1 + len(root.descendants())


def _empty_semantic_tree(language: str) -> SemanticNode:
    from intentumdiff.rust_core import _c_abi_call
    return SemanticNode.model_validate(_c_abi_call("empty_semantic_tree", language))


def _markdown_presentation(presented: PresentationResult, *, old_source: str, new_source: str,
                           old_filename: str, new_filename: str, phase: str) -> PresentationResult:
    """DTO adapter for complete Rust reconciliation; core errors propagate."""
    from intentumdiff.rust_core import _c_abi_call
    result = _c_abi_call("reconcile_markdown", {
        "changes": [change.model_dump(mode="json") for change in presented.changes],
        "change_groups": [group.model_dump(mode="json") for group in presented.change_groups],
        "ignored_style_changes": presented.ignored_style_changes,
    }, old_source, new_source, old_filename, new_filename, phase)
    return PresentationResult(
        changes=[Change.model_validate(value) for value in result["changes"]],
        change_groups=[ChangeGroup.model_validate(value) for value in result["change_groups"]],
        ignored_style_changes=result["ignored_style_changes"],
    )


def _markdown_section_move_presentation(presented: PresentationResult, **kwargs: Any) -> PresentationResult:
    return _markdown_presentation(presented, phase="moves", **kwargs)


def _markdown_section_heading_rename_presentation(presented: PresentationResult, **kwargs: Any) -> PresentationResult:
    return _markdown_presentation(presented, phase="renames", **kwargs)


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


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


def _enrich_literal_labels(root: SemanticNode, source: str) -> SemanticNode:
    """Delegate literal-label enrichment to Rust; never reconstruct semantics."""
    from intentumdiff.rust_core import enrich_literal_labels

    return enrich_literal_labels(root, source)
