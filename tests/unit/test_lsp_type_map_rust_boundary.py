"""Rust C ABI ownership of LSP type-tree attachment (Python #155, Rust #181)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from intentumdiff import rust_core
from intentumdiff.core.models import NodePosition, SemanticNode


def _node(node_id: str, *, line: int = 0, children: list[SemanticNode] | None = None) -> SemanticNode:
    return SemanticNode(
        id=node_id,
        node_type="identifier",
        label=node_id,
        position=NodePosition(start_line=line, start_col=1, end_line=line, end_col=6),
        structural_hash="hash-" + node_id,
        children=children or [],
    )


def test_native_type_map_attachment_preserves_positions_and_duplicate_ids() -> None:
    # A shared ID must annotate both occurrences; unrelated nested nodes stay untouched.
    root = _node("root", children=[
        _node("dup"),
        _node("parent", line=1, children=[_node("dup", line=2), _node("untouched", line=3)]),
    ])
    original = root.model_dump(mode="json")
    updated = rust_core.attach_lsp_type_map(root, {"dup": "str | None", "root": "module"})
    assert updated.type_info == "module"
    assert updated.children[0].type_info == "str | None"
    assert updated.children[1].children[0].type_info == "str | None"
    assert updated.children[1].children[1].type_info is None
    assert updated.children[0].position == root.children[0].position
    assert updated.children[1].children[1].model_dump() == root.children[1].children[1].model_dump()
    assert root.model_dump(mode="json") == original  # input DTO never modified


def test_native_type_map_empty_mapping_is_a_genuine_noop() -> None:
    root = _node("root", children=[_node("child")])
    assert rust_core.attach_lsp_type_map(root, {}).model_dump() == root.model_dump()


def test_native_type_map_invalid_value_fails_closed() -> None:
    root = _node("root")
    malformed = cast(dict[str, str], {"root": None})
    with pytest.raises(RuntimeError, match="lsp_attach_type_map_json"):
        rust_core.attach_lsp_type_map(root, malformed)


def test_missing_native_type_map_handler_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="required handler is unavailable"):
        rust_core.attach_lsp_type_map(_node("root"), {"root": "int"})


def test_malformed_native_tree_dto_is_not_silently_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust_core, "_required_engine_json", lambda *_args, **_kw: {"children": []})
    with pytest.raises(RuntimeError, match="invalid tree DTO"):
        rust_core.attach_lsp_type_map(_node("root"), {"root": "int"})


def test_python_indexer_has_no_type_tree_mutation_function() -> None:
    import ast
    from pathlib import Path

    from intentumdiff.core import indexer

    source = Path(indexer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    definitions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_attach_type_info" not in definitions
    assert "attach_lsp_type_map(node, type_map)" in source
