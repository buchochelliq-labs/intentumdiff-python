"""Cross-file scenarios — refactors/moves that span more than one file.

These can't be expressed by the single-file standard matrix (scenario_suite.py): a symbol moving
from one module to another, or a module splitting into several, is detected at the commit/index
level by ``analysis/cross_file.py``. Built on hand-constructed symbol trees + ``SemanticIndex`` so
no git repo is needed (the same detection ``CommitDiffer`` uses).
"""

from __future__ import annotations

from intentdiff.analysis.cross_file import detect_cross_file_changes
from intentdiff.core.index import SemanticIndex
from intentdiff.core.models import ChangeType, CrossFileChange, NodePosition, SemanticNode


def _pos() -> NodePosition:
    return NodePosition(start_line=0, start_col=0, end_line=1, end_col=0)


def _fn(node_id: str, name: str) -> SemanticNode:
    body = SemanticNode(
        id=f"{node_id}.body", node_type="block", label="block",
        position=_pos(), structural_hash=f"body-{name}", children=[],
    )
    return SemanticNode(
        id=node_id, node_type="function_definition", label=name,
        position=_pos(), structural_hash=f"fn-{name}", children=[body],
    )


def _module(node_id: str, *fns: SemanticNode) -> SemanticNode:
    # The module label is the scope in a symbol's qualified name, so it must be CONSTANT across
    # old/new (a moved symbol keeps its qualified name; only its file changes — that's what
    # MOVE_TO_MODULE keys on). ``node_id`` only varies the node identity.
    return SemanticNode(
        id=node_id, node_type="module", label="module",
        position=_pos(), structural_hash=f"mod-{node_id}", children=list(fns),
    )


def _index(*files: tuple[str, SemanticNode]) -> SemanticIndex:
    index = SemanticIndex()
    for filename, tree in files:
        index.add_tree(filename, "python", tree)
    return index.build()


def _of_type(changes: list[CrossFileChange], change_type: ChangeType) -> list[CrossFileChange]:
    return [c for c in changes if c.change_type == change_type]


def test_cross_file_move_to_module() -> None:
    # `foo` lived in a.py; now it lives in b.py, body unchanged. Expect one MOVE_TO_MODULE.
    old = _index(("a.py", _module("oa", _fn("oa.foo", "foo"))), ("b.py", _module("ob")))
    new = _index(("a.py", _module("na")), ("b.py", _module("nb", _fn("nb.foo", "foo"))))

    changes = detect_cross_file_changes(old, new)
    moves = _of_type(changes, ChangeType.MOVE_TO_MODULE)
    foo_move = [c for c in moves if "foo" in c.symbol_name]
    assert len(foo_move) == 1, f"expected one MOVE_TO_MODULE for foo, got {[(c.symbol_name, c.old_file, c.new_file) for c in moves]}"
    assert foo_move[0].old_file == "a.py"
    assert foo_move[0].new_file == "b.py"
    assert foo_move[0].confidence >= 0.9


def test_cross_file_split_module() -> None:
    # a.py held foo + bar; they split into b.py and c.py. Expect a SPLIT_MODULE (or the two
    # constituent MOVE_TO_MODULE moves that make it up).
    old = _index(
        ("a.py", _module("oa", _fn("oa.foo", "foo"), _fn("oa.bar", "bar"))),
        ("b.py", _module("ob")),
        ("c.py", _module("oc")),
    )
    new = _index(
        ("a.py", _module("na")),
        ("b.py", _module("nb", _fn("nb.foo", "foo"))),
        ("c.py", _module("nc", _fn("nc.bar", "bar"))),
    )

    changes = detect_cross_file_changes(old, new)
    split = _of_type(changes, ChangeType.SPLIT_MODULE)
    moves = _of_type(changes, ChangeType.MOVE_TO_MODULE)
    foo_bar_moves = [c for c in moves if c.symbol_name.endswith(("foo", "bar")) and c.old_file == "a.py"]
    assert split or len(foo_bar_moves) >= 2, (
        f"expected a SPLIT_MODULE or the two constituent moves out of a.py; "
        f"split={[c.symbol_name for c in split]} moves={[(c.symbol_name, c.old_file, c.new_file) for c in moves]}"
    )
