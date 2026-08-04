"""
tests/benchmarks/helpers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared tree-building utilities for benchmark suites.  All helpers are pure
functions (no fixtures) so they can be imported freely by conftest.py and
individual test modules.
"""

from __future__ import annotations

import hashlib
import itertools

from intentumdiff.core.models import Change, ChangeType, NodePosition, SemanticNode

# Module-level counter ensures unique IDs across all tree instances created
# within a single test session.
_id_counter: itertools.count[int] = itertools.count(1)


def _next_id() -> str:
    return str(next(_id_counter))


def _pos(line: int = 1) -> NodePosition:
    return NodePosition(start_line=line, start_col=0, end_line=line, end_col=10)


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def make_leaf(
    node_type: str = "identifier",
    label: str | None = None,
    line: int = 1,
) -> SemanticNode:
    """Create a leaf SemanticNode with a unique id and structural_hash."""
    nid = _next_id()
    lbl = label if label is not None else f"var_{nid}"
    return SemanticNode(
        id=nid,
        node_type=node_type,
        label=lbl,
        position=_pos(line),
        structural_hash=_hash(node_type, lbl, nid),
    )


def make_tree(
    n_levels: int,
    branching: int = 3,
    node_type: str = "block",
) -> SemanticNode:
    """
    Build a balanced synthetic tree.

    Node count = (branching^(n_levels+1) - 1) / (branching - 1).
    Examples (branching=3):
      n_levels=2  ->  13 nodes
      n_levels=3  ->  40 nodes
      n_levels=4  -> 121 nodes
      n_levels=5  -> 364 nodes
      n_levels=6  -> 1093 nodes
    """
    if n_levels == 0:
        return make_leaf(node_type=node_type)
    children = [make_tree(n_levels - 1, branching, node_type) for _ in range(branching)]
    nid = _next_id()
    child_hashes = "|".join(c.structural_hash for c in children)
    return SemanticNode(
        id=nid,
        node_type=node_type,
        label=f"block_{nid}",
        position=_pos(),
        structural_hash=_hash(node_type, child_hashes),
        children=children,
    )


def make_tree_hetero(
    n_levels: int,
    branching: int = 3,
    node_types: tuple[str, ...] | list[str] = (
        "function_definition",
        "assignment",
        "call_expression",
        "return_statement",
        "identifier",
    ),
) -> SemanticNode:
    """
    Build a balanced synthetic tree where the node type at each depth level
    cycles through ``node_types``.

    Using depth-based type assignment means every node at a given depth shares
    the same type, which exercises the ``node_type``-bucketing optimisation in
    ``_bottom_up_match`` more realistically than a single-type tree (each
    candidate search is confined to a same-type subset instead of the full new
    tree).

    Node count is identical to ``make_tree(n_levels, branching)``.
    """
    node_type = node_types[n_levels % len(node_types)]
    if n_levels == 0:
        return make_leaf(node_type=node_type)
    children = [
        make_tree_hetero(n_levels - 1, branching, node_types)
        for _ in range(branching)
    ]
    nid = _next_id()
    child_hashes = "|".join(c.structural_hash for c in children)
    return SemanticNode(
        id=nid,
        node_type=node_type,
        label=f"{node_type}_{nid}",
        position=_pos(),
        structural_hash=_hash(node_type, child_hashes),
        children=children,
    )


def make_change(
    change_type: ChangeType,
    node_type: str = "function_definition",
    label: str | None = None,
) -> Change:
    """Create a synthetic Change for analysis-pass benchmarks."""
    leaf = make_leaf(node_type=node_type, label=label)
    if change_type == ChangeType.DELETION:
        return Change(change_type=change_type, old_node=leaf)
    return Change(change_type=change_type, new_node=leaf)


# ---------------------------------------------------------------------------
# Synthetic Python source generator (shared by e2e and fuel/scale benchmarks)
# ---------------------------------------------------------------------------

def make_python_source(n_functions: int, modified: bool = False) -> str:
    """
    Generate a synthetic Python module with ``n_functions`` top-level functions.

    Each function body is ~15 lines, so the module is approximately
    ``n_functions * 16`` lines long (including blank separators).

    When ``modified=True`` the following edits are applied (simulating a
    realistic commit):

    - Every 10th function is renamed  (``func_N`` → ``func_N_updated``)
    - Every 25th function is deleted  (~4 % of functions)
    - 50 new trivial functions are appended at the bottom
    - Every 5th function (``i % 5 == 1``) has one body expression changed
    """
    lines: list[str] = [
        "from __future__ import annotations",
        "from typing import List, Dict",
        "import hashlib",
        "import itertools",
        "",
    ]
    for i in range(n_functions):
        if modified and i % 25 == 0 and i > 0:
            continue  # simulate deletion (~4 %)
        name = f"func_{i}_updated" if (modified and i % 10 == 0) else f"func_{i}"
        v = f"v{i}"
        body_extra = (
            f"    {v}_f = {v}_c * 2"
            if (modified and i % 5 == 1)
            else f"    {v}_f = {v}_c + 1"
        )
        lines += [
            f"def {name}(x: int, y: str, z: float = 1.0) -> bool:",
            f"    {v}_a = x + len(y)",
            f"    {v}_b = list(range({v}_a))",
            f"    {v}_c: int = 0",
            f"    for _item in {v}_b:",
            f"        if _item % 2 == 0:",
            f"            {v}_c += _item",
            f"        else:",
            f"            {v}_c -= _item",
            f"    {v}_d = str({v}_c * z)",
            f"    {v}_e = hashlib.md5({v}_d.encode()).hexdigest()",
            f"    if not {v}_e:",
            f"        return False",
            body_extra,
            f"    return {v}_f > 0",
            "",
        ]
    if modified:
        for i in range(n_functions, n_functions + 50):
            lines += [
                f"def new_func_{i}(a: int, b: int) -> int:",
                f"    return a + b",
                "",
            ]
    return "\n".join(lines)
