"""Tree, markdown-presentation, fallback-diff and stream helpers for intentumdiff.differ."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from intentumdiff.analysis.text_review import (
    PresentationResult,
)
from intentumdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeStreamEvent,
    ChangeStreamPhase,
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
    from intentumdiff.rust_core import enrich_literal_labels

    return enrich_literal_labels(root, source)


def _changes_to_stream_events(
    before: list[Change],
    after: list[Change],
    phase: ChangeStreamPhase,
) -> Iterator[ChangeStreamEvent]:
    """Diff two successive change lists and emit ``ChangeStreamEvent`` objects.

    Uses Python object identity to detect which changes were consumed
    (present in *before* but not *after*) and which were added (present in
    *after* but not *before*).  ``Change`` is a frozen Pydantic model, so
    every logical change is a distinct Python object even when its content is
    the same as another change.

    For each new change in *after*:

    * If the new change's ``old_node.id`` or ``new_node.id`` matches a node ID
      from a consumed *before* change, the event is emitted as ``action="revise"``
      with ``replaced_ids`` listing the consumed node IDs.
    * Otherwise, ``action="add"`` is emitted (a brand-new change with no
      predecessor in *before*).

    Any change that was consumed but not referenced by a new change is emitted
    as ``action="remove"`` with ``replaced_ids=[node_id]``.
    """
    before_by_pyid = {id(c): c for c in before}
    after_by_pyid = {id(c): c for c in after}

    consumed = [c for pyid, c in before_by_pyid.items() if pyid not in after_by_pyid]
    added = [c for pyid, c in after_by_pyid.items() if pyid not in before_by_pyid]

    # Build node-id → consumed-change lookup.
    consumed_node_ids: dict[str, Change] = {}
    for c in consumed:
        if c.old_node:
            consumed_node_ids[c.old_node.id] = c
        if c.new_node:
            consumed_node_ids[c.new_node.id] = c

    mentioned: set[str] = set()

    for new_change in added:
        replaced_ids: list[str] = []
        if new_change.old_node and new_change.old_node.id in consumed_node_ids:
            replaced_ids.append(new_change.old_node.id)
            mentioned.add(new_change.old_node.id)
        if new_change.new_node and new_change.new_node.id in consumed_node_ids:
            replaced_ids.append(new_change.new_node.id)
            mentioned.add(new_change.new_node.id)
        yield ChangeStreamEvent(
            phase=phase,
            action="revise" if replaced_ids else "add",
            replaced_ids=replaced_ids,
            change=new_change,
        )

    # Emit "remove" for consumed changes not referenced by any new change.
    for c in consumed:
        node_id = c.old_node.id if c.old_node else c.new_node.id if c.new_node else None
        if node_id and node_id not in mentioned:
            yield ChangeStreamEvent(
                phase=phase,
                action="remove",
                replaced_ids=[node_id],
            )

