"""
intentdiff.lsp_server._codelens
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Translate a ``SemanticDiff`` into a list of LSP ``CodeLens`` objects.
"""

from __future__ import annotations

from lsprotocol import types as lsp_types

from intentdiff.core.models import Change, ChangeType, SemanticDiff, SemanticNode

# Change types that are surfaced as code lenses.
_CODELENS_CHANGE_TYPES: frozenset[str] = frozenset({
    ChangeType.REFACTORING,
    ChangeType.MOVE,
    ChangeType.MOVE_TO_MODULE,
    ChangeType.CROSS_FILE_RENAME,
    ChangeType.REORDER,
})


def node_to_lsp_range(node: SemanticNode) -> lsp_types.Range:
    """Convert a ``SemanticNode``'s position to an LSP ``Range`` (0-indexed)."""
    pos = node.position
    return lsp_types.Range(
        start=lsp_types.Position(line=pos.start_line, character=pos.start_col),
        end=lsp_types.Position(line=pos.end_line, character=pos.end_col),
    )


def _codelens_title(change: Change) -> str:
    """Build the display title for a code-lens entry."""
    if change.refactoring_kind is not None:
        return f"\u21bb {change.refactoring_kind.name}: {change.description}"
    ct = change.change_type
    name = ct.name if isinstance(ct, ChangeType) else str(ct)
    return f"\u27f3 {name}: {change.description}"


def semantic_diff_to_codelens(diff: SemanticDiff) -> list[lsp_types.CodeLens]:
    """Translate *diff* into a list of display-only LSP code lenses.

    Emitted for changes whose ``change_type`` is in
    ``{REFACTORING, MOVE, MOVE_TO_MODULE, CROSS_FILE_RENAME, REORDER}``.
    The ``command`` field is left empty (display-only; no action on click).
    """
    lenses: list[lsp_types.CodeLens] = []

    for change in diff.changes:
        if change.change_type not in _CODELENS_CHANGE_TYPES:
            continue
        node = change.new_node or change.old_node
        if node is None:
            continue
        lenses.append(
            lsp_types.CodeLens(
                range=node_to_lsp_range(node),
                command=lsp_types.Command(
                    title=_codelens_title(change),
                    command="",
                ),
            )
        )

    return lenses
