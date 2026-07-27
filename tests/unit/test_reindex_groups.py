"""Generic-text group index-space contract (analysis/text_review.py).

The ``_reindex_groups_to_final_changes`` tests retired with the transitional
presentation layer (issue #57 payoff, stage 4b) — the Rust finalize owns the
final index space.

Groups are assembled from stages whose ``raw_change_indices`` live in different
index spaces (``presentation_input`` / ``mixed`` / ``final_changes``). Consumers
index straight into the *final* ``changes`` list, so a stale/cross-space index
collides with an unrelated final change — which mislabelled a genuinely new
function as "noise". The reindex remaps every group by node identity so it
addresses only the final changes it actually owns.
"""

from __future__ import annotations

from intentdiff.analysis.text_review import normalize_generic_text_for_review
from intentdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeGroupKind,
    ChangeType,
    NodePosition,
    SemanticNode,
)


def _node(node_id: str, label: str) -> SemanticNode:
    return SemanticNode(
        id=node_id,
        node_type="function_definition",
        label=label,
        position=NodePosition(start_line=0, start_col=0, end_line=1, end_col=0),
        structural_hash=f"hash-{node_id}",
    )


def _addition(node_id: str, label: str) -> Change:
    return Change(change_type=ChangeType.ADDITION, new_node=_node(node_id, label))


def test_generic_text_noise_group_owns_no_final_change_at_source() -> None:
    # A generic-parser file (e.g. .gitignore) where raw token changes are replaced by a
    # single stable line-span insert. The NOISE_SUPPRESSED group owns no final change (the
    # originals are discarded), so it must emit empty raw_change_indices *at the source* —
    # not phantom indices that collide with the real insert and bury it under "noise".
    original_tokens = [_addition(f"tok{i}", f"tok{i}") for i in range(3)]
    result = normalize_generic_text_for_review(
        original_tokens,
        "a\nb\n",
        "a\nb\nc\n",
    )
    (group,) = result.change_groups
    assert group.raw_change_indices == []  # honest empty at the source, no phantom indices.
    assert group.metadata["suppressed_count"] == 3  # "(3 hidden)" label preserved.
    assert "index_space" not in group.metadata  # no band-aid tag needed.
    assert any(c.change_type == ChangeType.ADDITION for c in result.changes)


