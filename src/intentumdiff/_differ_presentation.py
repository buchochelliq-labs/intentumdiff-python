"""Tree, markdown-presentation, fallback-diff and stream helpers for intentumdiff.differ."""

from __future__ import annotations

import hashlib
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
    ChangeGroupKind,
    ChangeStreamEvent,
    ChangeStreamPhase,
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


def _all_semantic_nodes(root: SemanticNode) -> list[SemanticNode]:
    return [root, *root.descendants()]


def _semantic_parent_map(root: SemanticNode) -> dict[str, SemanticNode]:
    parents: dict[str, SemanticNode] = {}
    for node in _all_semantic_nodes(root):
        for child in node.children:
            if child.id != node.id:
                parents[child.id] = node
    return parents


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


def _annotate_text_diffs(changes: list[Change]) -> list[Change]:
    """
    Annotate leaf-node MODIFICATION changes with an inline character-level diff.

    For each ``MODIFICATION`` change where both nodes are leaves with different
    labels, populates ``Change.text_diff`` with a compact ``[-old][+new]``
    annotation using ``difflib.SequenceMatcher``.  Truncated to 200 characters.
    Already-annotated changes (``text_diff is not None``) are left unchanged.
    """
    import difflib

    result: list[Change] = []
    for change in changes:
        if (
            change.change_type == ChangeType.MODIFICATION
            and change.old_node is not None
            and change.new_node is not None
            and change.old_node.is_leaf()
            and change.new_node.is_leaf()
            and change.old_node.label != change.new_node.label
            and change.text_diff is None
        ):
            old_label = change.old_node.label
            new_label = change.new_node.label
            matcher = difflib.SequenceMatcher(None, old_label, new_label, autojunk=False)
            parts: list[str] = []
            for op, i1, i2, j1, j2 in matcher.get_opcodes():
                if op == "equal":
                    parts.append(old_label[i1:i2])
                elif op == "replace":
                    parts.append(f"[-{old_label[i1:i2]}][+{new_label[j1:j2]}]")
                elif op == "delete":
                    parts.append(f"[-{old_label[i1:i2]}]")
                elif op == "insert":
                    parts.append(f"[+{new_label[j1:j2]}]")
            text_diff: str = "".join(parts)
            if len(text_diff) > 200:
                text_diff = text_diff[:197] + "…"
            change = change.model_copy(update={"text_diff": text_diff})
        result.append(change)
    return result


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


_GENERIC_STRING_LABELS = frozenset({"string", "string_literal"})

# Entity node types whose labels should surface in review output even when
# the matcher preserved the container (its body changed in place but the
# container itself was matched by label). Broad coverage across all
# supported parsers — tree-sitter grammars plus the lightweight hand-written
# parsers (graphql, po, asciidoc, latex, ocaml, reasonml). See
# ``the retired NOISE_SUPPRESSION_RETUNE doc (git history)`` Step A2.
_NAMED_ENTITY_NODE_TYPES: frozenset[str] = frozenset(
    {
        # Tree-sitter common
        "function_definition", "async_function_def", "class_definition",
        "method_declaration", "function_declaration", "async_function_declaration",
        "constructor_declaration",
        "destructor_declaration", "property_declaration", "field_declaration",
        "variable_declaration", "lexical_declaration", "struct_definition",
        "interface_declaration", "enum_declaration", "enum_member_declaration",
        "operator_declaration", "event_declaration", "delegate_declaration",
        "record_declaration", "record_struct_declaration",
        # GraphQL
        "type_definition", "operation_definition", "fragment_definition",
        "field_definition", "directive_definition", "schema_definition",
        "enum_definition", "input_object_definition",
        # PO/gettext
        "message", "obsolete_message",
        # AsciiDoc
        "section_level_1", "section_level_2", "section_level_3",
        "section_level_4", "section_level_5", "section_level_6",
        # LaTeX
        "document_class", "package", "section", "environment",
        # OCaml / ReasonML
        "module_binding", "module", "value_definition",
        "recursive_value", "component", "signature_value", "class_type",
        "exception",
    }
)


def _is_named_entity_node(node: SemanticNode) -> bool:
    """Check whether a node is a named entity whose label should surface in review."""
    if not node.label or node.label == node.node_type:
        return False
    return (
        node.node_type in _NAMED_ENTITY_NODE_TYPES
        or node.node_type.endswith("_definition")
        or node.node_type.endswith("_declaration")
    )


def _surface_changed_in_place_entities(
    *,
    matching: Any,
    changes: list[Change],
) -> list[ChangeGroup]:
    """Emit MEANINGFUL_CHANGE groups for named entities whose body changed in place.

    When the matcher preserves a named entity (e.g. ``type User``) whose body
    changed, the entity itself doesn't appear in the change list — only its
    changed descendants do. This function scans the matching for such pairs
    and emits change-groups carrying the entity's label, so review UIs and
    entity-label assertions can surface the container name.

    Guards:
      - Both endpoints must be named entities (``_is_named_entity_node``).
      - At least one descendant of the pair must appear in the change list
        (the container genuinely changed — detected via observable changes
        rather than structural_hash, which may be unreliable for some
        parser-produced trees).
      - The pair's old/new IDs must not already appear in any top-level
        change event (avoid duplicating already-surfaced entities).
    """
    if not matching:
        return []

    # Collect IDs that already surface in the change list.
    surfaced_old_ids: set[str] = set()
    surfaced_new_ids: set[str] = set()
    for change in changes:
        if change.old_node is not None:
            surfaced_old_ids.add(change.old_node.id)
        if change.new_node is not None:
            surfaced_new_ids.add(change.new_node.id)

    groups: list[ChangeGroup] = []
    for pair in matching:
        old_node = getattr(pair, "old_node", None)
        new_node = getattr(pair, "new_node", None)
        if old_node is None or new_node is None:
            continue
        if not _is_named_entity_node(old_node) or not _is_named_entity_node(new_node):
            continue
        # Skip if either endpoint already surfaces in a top-level change.
        if old_node.id in surfaced_old_ids or new_node.id in surfaced_new_ids:
            continue
        # Check if any descendant of this entity pair appears in the change
        # list. This is the reliable signal that the container changed in
        # place — structural_hash comparison alone is insufficient for some
        # parser-produced trees where the hash doesn't capture content changes.
        old_desc_ids = {d.id for d in old_node.descendants()}
        new_desc_ids = {d.id for d in new_node.descendants()}
        has_changed_descendant = bool(
            old_desc_ids & surfaced_old_ids or new_desc_ids & surfaced_new_ids
        )
        if not has_changed_descendant:
            continue
        groups.append(
            ChangeGroup(
                kind=ChangeGroupKind.MEANINGFUL_CHANGE,
                raw_change_indices=[],
                old_labels=[old_node.label],
                new_labels=[new_node.label],
                old_node_ids=[old_node.id],
                new_node_ids=[new_node.id],
                confidence=0.9,
                rule_id="presentation.surface_changed_in_place_entity",
                metadata={
                    "entity_type": old_node.node_type,
                    "old_label": old_node.label,
                    "new_label": new_node.label,
                },
            )
        )
    return groups


def _slice_source_text(source: str, position: NodePosition) -> str:
    lines = source.splitlines()
    if position.start_line < 0 or position.start_line >= len(lines):
        return ""
    if position.end_line < position.start_line:
        return ""
    if position.start_line == position.end_line:
        return lines[position.start_line][position.start_col : position.end_col]

    selected = [lines[position.start_line][position.start_col :]]
    for line_no in range(position.start_line + 1, min(position.end_line, len(lines))):
        selected.append(lines[line_no])
    if position.end_line < len(lines):
        selected.append(lines[position.end_line][: position.end_col])
    return "\n".join(selected)


def _clean_string_literal_label(text: str) -> str:
    value = text.strip()
    while value and value[0] in "rRuUbBfF@$":
        value = value[1:]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        value = value[1:-1]
    return value.strip()


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

