"""Unit tests for :mod:`intentumdiff.lsp.enricher`."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from intentumdiff.core.models import NodePosition, SemanticNode
from intentumdiff.lsp.enricher import TypeEnricher
from intentumdiff.lsp.exceptions import LspConnectionError, LspTimeoutError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _node(
    node_id: str,
    node_type: str,
    label: str = "",
    children: list[SemanticNode] | None = None,
    line: int = 0,
    col: int = 0,
) -> SemanticNode:
    pos = NodePosition(start_line=line, start_col=col, end_line=line, end_col=col + 1)
    return SemanticNode(
        id=node_id,
        node_type=node_type,
        label=label or node_id,
        position=pos,
        structural_hash=node_id,
        children=children or [],
    )


def _make_client(hover_map: dict[tuple[int, int], str | None]) -> MagicMock:
    client = MagicMock()

    async def _hover(uri: str, line: int, col: int) -> str | None:
        return hover_map.get((line, col))

    client.hover = AsyncMock(side_effect=_hover)
    client.did_open = AsyncMock()
    client.did_close = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Tests: _collect_hover_targets
# ---------------------------------------------------------------------------


def test_collect_hover_targets_finds_function_name_leaf() -> None:
    """A leaf node with a _NAME_TYPES type produces a (leaf, leaf) pair."""
    leaf = _node("n1", "function_name")
    root = _node("root", "module", children=[leaf])
    enricher = TypeEnricher(_make_client({}), "python")  # type: ignore[arg-type]
    targets = enricher._collect_hover_targets(root)
    result_ids = [rn.id for rn, _pn in targets]
    assert "n1" in result_ids


def test_collect_hover_targets_ignores_non_name_types() -> None:
    """Unrecognised node types are not collected."""
    leaf = _node("n1", "string_literal")
    root = _node("root", "module", children=[leaf])
    enricher = TypeEnricher(_make_client({}), "python")  # type: ignore[arg-type]
    targets = enricher._collect_hover_targets(root)
    assert not any(rn.id == "n1" for rn, _pn in targets)


def test_collect_hover_targets_decl_node_uses_name_leaf_position() -> None:
    """For assignment nodes the result id is the decl node, position is the name leaf."""
    name_leaf = _node("name", "variable_name", line=3, col=4)
    decl = _node("decl1", "assignment", children=[name_leaf])
    root = _node("root", "module", children=[decl])
    enricher = TypeEnricher(_make_client({}), "python")  # type: ignore[arg-type]
    targets = enricher._collect_hover_targets(root)
    decl_targets = [(rn, pn) for rn, pn in targets if rn.id == "decl1"]
    assert decl_targets, "expected assignment node in targets"
    _rn, pn = decl_targets[0]
    assert pn.id == "name", "position node should be the name leaf"


def test_collect_hover_targets_ignores_non_leaf_name_types() -> None:
    """A _NAME_TYPES node that has children is not added as a plain leaf target."""
    inner = _node("inner", "function_name")
    non_leaf = _node("outer", "function_name", children=[inner])
    root = _node("root", "module", children=[non_leaf])
    enricher = TypeEnricher(_make_client({}), "python")  # type: ignore[arg-type]
    targets = enricher._collect_hover_targets(root)
    result_ids = [rn.id for rn, _pn in targets]
    # inner is a leaf → included; outer is not a leaf → not added as name-leaf target
    assert "inner" in result_ids
    assert "outer" not in result_ids


# ---------------------------------------------------------------------------
# Tests: enrich
# ---------------------------------------------------------------------------


def test_enrich_maps_position_to_node_id() -> None:
    leaf = _node("n1", "function_name", line=5, col=3)
    root = _node("root", "module", children=[leaf])
    client = _make_client({(5, 3): "int"})

    result = run(TypeEnricher(client, "python").enrich("/tmp/test.py", "x: int = 1", root))  # type: ignore[arg-type]
    assert result == {"n1": "int"}


def test_enrich_multiple_nodes() -> None:
    a = _node("a", "function_name", line=0, col=0)
    b = _node("b", "class_name", line=1, col=0)
    c = _node("c", "string_literal", line=2, col=0)  # not a name type
    root = _node("root", "module", children=[a, b, c])
    client = _make_client({(0, 0): "str", (1, 0): "int", (2, 0): "never"})

    result = run(TypeEnricher(client, "python").enrich("/tmp/test.py", "", root))  # type: ignore[arg-type]
    assert result == {"a": "str", "b": "int"}


def test_enrich_returns_empty_on_connection_error() -> None:
    leaf = _node("n1", "function_name", line=0, col=0)
    root = _node("root", "module", children=[leaf])
    client = MagicMock()
    client.did_open = AsyncMock(side_effect=LspConnectionError("refused"))

    result = run(TypeEnricher(client, "python").enrich("/tmp/test.py", "", root))  # type: ignore[arg-type]
    assert result == {}


def test_enrich_skips_nodes_on_timeout() -> None:
    a = _node("a", "function_name", line=0, col=0)
    b = _node("b", "function_name", line=1, col=0)
    root = _node("root", "module", children=[a, b])

    async def _hover(uri: str, line: int, col: int) -> str | None:
        if line == 0:
            raise LspTimeoutError("timeout")
        return "float"

    client = MagicMock()
    client.did_open = AsyncMock()
    client.did_close = AsyncMock()
    client.hover = AsyncMock(side_effect=_hover)

    result = run(TypeEnricher(client, "python").enrich("/tmp/test.py", "", root))  # type: ignore[arg-type]
    assert "a" not in result
    assert result.get("b") == "float"


def test_enrich_empty_tree() -> None:
    root = _node("root", "module")
    result = run(TypeEnricher(_make_client({}), "python").enrich("/tmp/test.py", "", root))  # type: ignore[arg-type]
    assert result == {}


def test_enrich_did_close_called_even_on_unexpected_error() -> None:
    """did_close is always called (finally block), even when hover raises."""
    leaf = _node("n1", "function_name", line=0, col=0)
    root = _node("root", "module", children=[leaf])

    client = MagicMock()
    client.did_open = AsyncMock()
    client.did_close = AsyncMock()
    client.hover = AsyncMock(side_effect=RuntimeError("unexpected"))

    result = run(TypeEnricher(client, "python").enrich("/tmp/test.py", "", root))  # type: ignore[arg-type]
    client.did_close.assert_called_once()
    assert result == {}

# ---------------------------------------------------------------------------
# Tests: Rust core parity (issue 100 S2 — hover-target collection in the core)
# ---------------------------------------------------------------------------


def test_rust_hover_target_collection_matches_python_walk() -> None:
    """`lsp_collect_hover_targets_json` mirrors `_collect_hover_targets` triple-for-triple
    on a tree exercising every rule: name leaves (own position), generic identifiers
    (excluded), declarations/parameters (first-name-leaf position under the DECL id),
    a declaration without a name leaf (skipped), a non-leaf name type (skipped), and
    duplicate ids (first pre-order occurrence wins)."""
    from intentumdiff.rust_core import try_rust_collect_hover_targets

    root = _node(
        "root",
        "module",
        children=[
            _node("f1", "function_name", line=1, col=4),
            _node("ident", "identifier", line=2, col=0),
            _node(
                "decl1",
                "assignment",
                children=[
                    _node("wrap", "target", children=[_node("v", "variable_name", line=5, col=2)]),
                    _node("lit", "integer", line=5, col=6),
                ],
            ),
            _node("decl2", "assignment", children=[_node("lit2", "integer", line=6, col=0)]),
            _node(
                "p1",
                "typed_parameter",
                children=[_node("pn", "name", line=7, col=8)],
            ),
            _node("outer", "function_name", children=[_node("inner", "method_name", line=8, col=1)]),
            _node("f1", "function_name", line=9, col=0),
        ],
    )

    rust_triples = try_rust_collect_hover_targets(root)
    if rust_triples is None:
        pytest.skip("rust core without lsp_collect_hover_targets_json")

    enricher = TypeEnricher(_make_client({}), "python")  # type: ignore[arg-type]
    python_triples = [
        (rn.id, pn.position.start_line, pn.position.start_col)
        for rn, pn in enricher._collect_hover_targets(root)
    ]
    assert rust_triples == python_triples
    # The scenario itself stays meaningful: the dedupe + decl mapping actually fired.
    assert ("decl1", 5, 2) in rust_triples
    assert ("p1", 7, 8) in rust_triples
    assert [t for t in rust_triples if t[0] == "f1"] == [("f1", 1, 4)]


def test_enricher_query_all_prefers_rust_triples(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_query_all` consumes the Rust triples when the core serves them — the Python walk
    is the fallback/oracle only."""
    import intentumdiff.rust_core as rust_core

    calls: list[str] = []
    monkeypatch.setattr(
        rust_core,
        "try_rust_collect_hover_targets",
        lambda root: calls.append("rust") or [("n1", 3, 4)],
    )
    client = _make_client({(3, 4): "int"})
    enricher = TypeEnricher(client, "python")  # type: ignore[arg-type]
    root = _node("root", "module")
    result = run(enricher.enrich("/tmp/a.py", "x = 1\n", root))
    assert result == {"n1": "int"}
    assert calls == ["rust"]
