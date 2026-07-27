"""
intentdiff.lsp_server._diagnostics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Translate a ``SemanticDiff`` into a list of LSP ``Diagnostic`` objects.
"""

from __future__ import annotations

from lsprotocol import types as lsp_types

from intentdiff.core.models import ChangeType, SemanticDiff, SemanticNode

# Sentinel range covering the whole document — used for parse-error diagnostics.
_FULL_DOC_RANGE = lsp_types.Range(
    start=lsp_types.Position(line=0, character=0),
    end=lsp_types.Position(line=2**31 - 1, character=0),
)


def node_to_lsp_range(node: SemanticNode) -> lsp_types.Range:
    """Convert a ``SemanticNode``'s position to an LSP ``Range``.

    ``NodePosition`` fields are already 0-indexed so no adjustment is needed.
    """
    pos = node.position
    return lsp_types.Range(
        start=lsp_types.Position(line=pos.start_line, character=pos.start_col),
        end=lsp_types.Position(line=pos.end_line, character=pos.end_col),
    )


def semantic_diff_to_diagnostics(diff: SemanticDiff) -> list[lsp_types.Diagnostic]:
    """Translate *diff* into a list of LSP diagnostics.

    * ``STYLE_ONLY`` changes → ``Hint`` severity (informational, on ``new_node``).
    * Parse errors → ``Warning`` severity (full-document range).
    """
    diags: list[lsp_types.Diagnostic] = []

    for change in diff.changes:
        if change.change_type == ChangeType.STYLE_ONLY and change.new_node is not None:
            diags.append(
                lsp_types.Diagnostic(
                    range=node_to_lsp_range(change.new_node),
                    severity=lsp_types.DiagnosticSeverity.Hint,
                    message="Style-only change — no semantic impact",
                    source="intentdiff",
                )
            )

    for err in diff.parse_errors:
        diags.append(
            lsp_types.Diagnostic(
                range=_FULL_DOC_RANGE,
                severity=lsp_types.DiagnosticSeverity.Warning,
                message=f"Parse error: {err}",
                source="intentdiff",
            )
        )

    return diags
