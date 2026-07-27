"""
tests/unit/test_cst_serializer.py
"""

from __future__ import annotations

import json
import pytest


class TestSerializeCst:
    def test_round_trips_to_dict(self):
        from intentdiff.core.cst_serializer import deserialize_cst, serialize_cst
        tree_sitter = pytest.importorskip(
            "tree_sitter", reason="parse-side debt dep (#82 split repo does not carry it)"
        )
        tspython = pytest.importorskip(
            "tree_sitter_python", reason="parse-side debt dep (#82 split repo does not carry it)"
        )

        lang = tree_sitter.Language(tspython.language())
        parser = tree_sitter.Parser(lang)
        src = b"x = 1\n"
        tree = parser.parse(src)
        json_str = serialize_cst(tree.root_node, src)
        data = deserialize_cst(json_str)
        assert data["type"] == "module"
        assert "children" in data or "text" in data

    def test_leaf_has_text(self):
        from intentdiff.core.cst_serializer import deserialize_cst, serialize_cst
        tree_sitter = pytest.importorskip(
            "tree_sitter", reason="parse-side debt dep (#82 split repo does not carry it)"
        )
        tspython = pytest.importorskip(
            "tree_sitter_python", reason="parse-side debt dep (#82 split repo does not carry it)"
        )

        lang = tree_sitter.Language(tspython.language())
        parser = tree_sitter.Parser(lang)
        src = b"42\n"
        tree = parser.parse(src)
        json_str = serialize_cst(tree.root_node, src)
        data = deserialize_cst(json_str)

        def find_leaf(node):
            if "text" in node:
                return node
            for child in node.get("children", []):
                result = find_leaf(child)
                if result:
                    return result
            return None

        leaf = find_leaf(data)
        assert leaf is not None
        assert "text" in leaf
