"""
tests/benchmarks/conftest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pytest fixtures shared across all benchmark modules.  Trees are constructed
with ``scope="module"`` so they are built once per test file and reused across
all benchmark iterations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intentdiff.core.models import Match, Matching
from intentdiff.core.models import ChangeType

from tests.benchmarks.helpers import make_change, make_tree, make_tree_hetero

_HETERO_TYPES = (
    "function_definition",
    "assignment",
    "call_expression",
    "return_statement",
    "identifier",
)

_BENCHMARK_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items):
    """Keep performance benchmarks out of default pytest release gates."""
    marker = pytest.mark.benchmark
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(_BENCHMARK_DIR):
            item.add_marker(marker)

# ---------------------------------------------------------------------------
# Engine fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_trees():
    """~40-node old + new trees with a synthetic partial matching (~9 matches)."""
    old_root = make_tree(3, branching=3)   # 40 nodes: 1+3+9+27
    new_root = make_tree(3, branching=3)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal)) // 2
    existing: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    return old_root, new_root, existing


@pytest.fixture(scope="module")
def medium_trees():
    """~364-node old + new trees with a synthetic partial matching (~45 matches)."""
    old_root = make_tree(5, branching=3)   # 364 nodes
    new_root = make_tree(5, branching=3)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal)) // 2
    existing: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    return old_root, new_root, existing


@pytest.fixture(scope="module")
def large_trees():
    """~1093-node old + new trees with a synthetic partial matching (~136 matches)."""
    old_root = make_tree(6, branching=3)   # 1093 nodes
    new_root = make_tree(6, branching=3)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal)) // 2
    existing: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    return old_root, new_root, existing


@pytest.fixture(scope="module")
def dice_inputs():
    """
    A (node_a, node_b, matching) triple for _dice micro-benchmarks.

    node_a / node_b: 121-node internal subtrees.
    matching: ~60 pairs drawn from their internal nodes (simulates a realistic
    mid-loop state in _bottom_up_match).
    """
    old_root = make_tree(4, branching=3)   # 121 nodes
    new_root = make_tree(4, branching=3)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal))
    matching: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    # Pick the root as node_a / node_b so they have many descendants.
    return old_root, new_root, matching


@pytest.fixture(scope="module")
def medium_trees_hetero():
    """~364-node old + new trees with mixed node types (~45 matches)."""
    old_root = make_tree_hetero(5, branching=3, node_types=_HETERO_TYPES)
    new_root = make_tree_hetero(5, branching=3, node_types=_HETERO_TYPES)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal)) // 2
    existing: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    return old_root, new_root, existing


@pytest.fixture(scope="module")
def large_trees_hetero():
    """~1093-node old + new trees with mixed node types (~136 matches)."""
    old_root = make_tree_hetero(6, branching=3, node_types=_HETERO_TYPES)
    new_root = make_tree_hetero(6, branching=3, node_types=_HETERO_TYPES)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal)) // 2
    existing: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    return old_root, new_root, existing


@pytest.fixture(scope="module")
def xlarge_trees():
    """~3280-node old + new trees with a synthetic partial matching (~410 matches)."""
    old_root = make_tree(7, branching=3)   # (3^8 - 1) / 2 = 3280 nodes
    new_root = make_tree(7, branching=3)
    old_nodes = [old_root] + old_root.descendants()
    new_nodes = [new_root] + new_root.descendants()
    old_internal = [n for n in old_nodes if not n.is_leaf()]
    new_internal = [n for n in new_nodes if not n.is_leaf()]
    n = min(len(old_internal), len(new_internal)) // 2
    existing: Matching = [Match(old_internal[i], new_internal[i]) for i in range(n)]
    return old_root, new_root, existing


# ---------------------------------------------------------------------------
# Analysis fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def large_moves_scenario():
    """
    200 DELETION + 200 INSERTION changes — 40 000 pairs, near the 100 000-pair cap.

    Insertions use slightly varied labels (``process_N_new``) so trigram
    comparison is non-trivial (not an early exact-match exit).
    """
    del_labels = [f"process_{i}" for i in range(200)]
    ins_labels = [f"process_{i}_new" for i in range(200)]
    deletions = [
        make_change(ChangeType.DELETION, "function_definition", lbl)
        for lbl in del_labels
    ]
    insertions = [
        make_change(ChangeType.ADDITION, "function_definition", lbl)
        for lbl in ins_labels
    ]
    return deletions, insertions


@pytest.fixture(scope="module")
def medium_moves_hetero():
    """
    60 DELETION + 60 INSERTION changes across 3 node types (cycling).

    The 3-type mix means the type-filter in promote_moves reduces the pair
    count from 3 600 to ~1 200, benchmarking the filter's real savings.
    Insertions use ``op_N_updated`` labels so trigram scoring is non-trivial.
    """
    _types = ["function_definition", "assignment", "call_expression"]
    del_labels = [f"op_{i}" for i in range(60)]
    ins_labels = [f"op_{i}_updated" for i in range(60)]
    deletions = [
        make_change(ChangeType.DELETION, _types[i % len(_types)], lbl)
        for i, lbl in enumerate(del_labels)
    ]
    insertions = [
        make_change(ChangeType.ADDITION, _types[i % len(_types)], lbl)
        for i, lbl in enumerate(ins_labels)
    ]
    return deletions, insertions
