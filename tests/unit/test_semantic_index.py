"""
tests/unit/test_semantic_index.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for ``SemanticIndex`` (``intentumdiff.core.index``).
"""

from __future__ import annotations

import json

import pytest

from intentumdiff.core.index import SemanticIndex
from intentumdiff.core.models import NodePosition, ReferenceKind, SemanticNode, SymbolDefinition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(sl: int = 0, sc: int = 0, el: int = 5, ec: int = 0) -> NodePosition:
    return NodePosition(start_line=sl, start_col=sc, end_line=el, end_col=ec)


def _leaf(id: str, node_type: str, label: str = "") -> SemanticNode:
    return SemanticNode(
        id=id,
        node_type=node_type,
        label=label,
        position=_pos(),
        structural_hash=f"h-{id}",
    )


def _node(
    id: str,
    node_type: str,
    label: str = "",
    children: list[SemanticNode] | None = None,
) -> SemanticNode:
    return SemanticNode(
        id=id,
        node_type=node_type,
        label=label,
        position=_pos(),
        structural_hash=f"h-{id}",
        children=children or [],
    )


def _call(id: str, label: str) -> SemanticNode:
    """A call-expression node (CALL reference kind)."""
    return _node(id, "call", label)


def _import(id: str, label: str) -> SemanticNode:
    """An import_statement node (IMPORT reference kind)."""
    return _node(id, "import_statement", label)


def _type_annotation(id: str, label: str) -> SemanticNode:
    """A type_annotation node (TYPE_USAGE reference kind)."""
    return _node(id, "type_annotation", label)


def _fn(id: str, label: str, children: list[SemanticNode] | None = None) -> SemanticNode:
    return _node(id, "function_definition", label, children)


def _cls(id: str, label: str, children: list[SemanticNode] | None = None) -> SemanticNode:
    return _node(id, "class_definition", label, children)


def _index_for(
    tree: SemanticNode,
    *,
    language: str = "python",
    filename: str = "code.txt",
) -> SemanticIndex:
    idx = SemanticIndex()
    idx.add_tree(filename, language, tree)
    idx.build()
    return idx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAddTree:
    def test_add_tree_stores_entry(self):
        idx = SemanticIndex()
        tree = _fn("1", "my_func")
        idx.add_tree("a.py", "python", tree)
        assert len(idx._files) == 1

    def test_add_tree_after_build_raises(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "foo"))
        idx.build()
        with pytest.raises(RuntimeError, match="already built"):
            idx.add_tree("b.py", "python", _fn("2", "bar"))


class TestBuild:
    def test_build_returns_self(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "foo"))
        result = idx.build()
        assert result is idx

    def test_build_extracts_top_level_function(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "my_func"))
        idx.build()
        assert "my_func" in idx.symbols

    def test_build_extracts_class_and_method(self):
        method = _node("2", "method_definition", "greet")
        cls = _cls("1", "Greeter", children=[method])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", cls)
        idx.build()
        assert "Greeter" in idx.symbols
        assert "Greeter.greet" in idx.symbols

    def test_build_skips_nodes_without_label(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _node("1", "function_definition", ""))
        idx.build()
        assert idx.symbols == {}

    def test_build_sets_correct_file(self):
        idx = SemanticIndex()
        idx.add_tree("utils.py", "python", _fn("1", "helper"))
        idx.build()
        defns = idx.symbols["helper"]
        assert defns[0].file == "utils.py"

    def test_build_sets_correct_language(self):
        idx = SemanticIndex()
        idx.add_tree("Foo.java", "java", _node("1", "class_declaration", "Foo"))
        idx.build()
        defns = idx.symbols["Foo"]
        assert defns[0].language == "java"

    def test_build_multiple_files(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "func_a"))
        idx.add_tree("b.py", "python", _fn("2", "func_b"))
        idx.build()
        assert "func_a" in idx.symbols
        assert "func_b" in idx.symbols

    def test_symbols_raises_before_build(self):
        idx = SemanticIndex()
        with pytest.raises(RuntimeError, match="Call build\\(\\) first"):
            _ = idx.symbols

    @pytest.mark.parametrize(
        ("language", "node_type", "label", "expected"),
        [
            ("powershell", "function_statement", "Add-Numbers", "Add-Numbers"),
            ("dart", "function_signature", "add", "add"),
            ("delphi", "defProc", "Add", "Add"),
            ("abap", "form", "GREET", "GREET"),
            ("abap", "function_module", "Z_ADD", "Z_ADD"),
            ("perl", "subroutine_declaration_statement", "greet", "greet"),
            ("ruby", "method", "greet", "greet"),
            ("vbnet", "module_block", "HelloWorld", "HelloWorld"),
            ("qsharp", "operation", "FlipBit", "FlipBit"),
            ("odin", "procedure_declaration", "add", "add"),
            ("haskell", "signature", "add", "add"),
            ("scala", "object_definition", "Main", "Main"),
            ("postscript", "procedure", "greet", "greet"),
            ("sas", "data_step", "WORK.GREETING", "WORK.GREETING"),
            ("wat", "func", "$add", "$add"),
        ],
    )
    def test_newer_language_definition_node_types_are_indexed(
        self,
        language: str,
        node_type: str,
        label: str,
        expected: str,
    ):
        idx = _index_for(_node("1", node_type, label), language=language)
        assert expected in idx.symbols
        assert idx.symbols[expected][0].node_type == node_type
        assert idx.symbols[expected][0].language == language

    def test_signature_is_not_indexed_outside_haskell(self):
        idx = _index_for(_node("1", "signature", "func add()"), language="swift")
        assert idx.symbols == {}

    def test_clojure_defn_list_is_indexed_but_plain_call_is_not_a_symbol(self):
        tree = _node(
            "0",
            "source",
            "source",
            [
                _node(
                    "0.0",
                    "list_lit",
                    "greet",
                    [_leaf("0.0.0", "sym_lit", "defn"), _leaf("0.0.1", "sym_lit", "greet")],
                ),
                _node(
                    "0.1",
                    "list_lit",
                    "println",
                    [_leaf("0.1.0", "sym_lit", "println")],
                ),
            ],
        )
        idx = _index_for(tree, language="clojure")
        assert "greet" in idx.symbols
        assert "println" not in idx.symbols

    def test_elixir_definition_calls_are_indexed_with_module_scope(self):
        tree = _node(
            "0",
            "source",
            "source",
            [
                _node(
                    "0.0",
                    "call",
                    "Greeter",
                    [
                        _leaf("0.0.0", "identifier", "defmodule"),
                        _node(
                            "0.0.1",
                            "do_block",
                            "do_block",
                            [
                                _node(
                                    "0.0.1.0",
                                    "call",
                                    "greet",
                                    [_leaf("0.0.1.0.0", "identifier", "def")],
                                )
                            ],
                        ),
                    ],
                )
            ],
        )
        idx = _index_for(tree, language="elixir")
        assert "Greeter" in idx.symbols
        assert "Greeter.greet" in idx.symbols


class TestFindDefinition:
    def test_find_existing(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "do_work"))
        idx.build()
        defns = idx.find_definition("do_work")
        assert len(defns) == 1
        assert defns[0].qualified_name == "do_work"

    def test_find_missing_returns_empty(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "do_work"))
        idx.build()
        assert idx.find_definition("nonexistent") == []

    def test_find_raises_before_build(self):
        idx = SemanticIndex()
        with pytest.raises(RuntimeError, match="Call build\\(\\) first"):
            idx.find_definition("foo")


class TestFindReferences:
    def test_call_node_extracted(self):
        """A call node's label becomes a CALL ReferenceUsage."""
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _call("1", "do_work"))
        idx.build()
        refs = idx.find_references("do_work")
        assert len(refs) == 1
        assert refs[0].reference_kind == ReferenceKind.CALL
        assert refs[0].file == "a.py"
        assert refs[0].language == "python"
        assert refs[0].qualified_name == "do_work"

    def test_import_node_extracted(self):
        """An import_statement node becomes an IMPORT ReferenceUsage."""
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _import("1", "os"))
        idx.build()
        refs = idx.find_references("os")
        assert len(refs) == 1
        assert refs[0].reference_kind == ReferenceKind.IMPORT

    def test_type_annotation_node_extracted(self):
        """A type_annotation node becomes a TYPE_USAGE ReferenceUsage."""
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _type_annotation("1", "MyType"))
        idx.build()
        refs = idx.find_references("MyType")
        assert len(refs) == 1
        assert refs[0].reference_kind == ReferenceKind.TYPE_USAGE

    def test_enclosing_scope_at_module_level(self):
        """A call at module scope (tree root is the call) has enclosing_scope=None."""
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _call("1", "helper"))
        idx.build()
        refs = idx.find_references("helper")
        assert refs[0].enclosing_scope is None

    def test_enclosing_scope_inside_function(self):
        """A call inside a function body gets the function's qualified name as scope."""
        inner_call = _call("2", "helper")
        fn = _fn("1", "my_func", children=[inner_call])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", fn)
        idx.build()
        refs = idx.find_references("helper")
        assert len(refs) == 1
        assert refs[0].enclosing_scope == "my_func"

    def test_enclosing_scope_inside_nested_method(self):
        """A call inside a method gets the fully-qualified Class.method scope."""
        inner_call = _call("3", "helper")
        method = _node("2", "method_definition", "greet", children=[inner_call])
        cls = _cls("1", "Greeter", children=[method])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", cls)
        idx.build()
        refs = idx.find_references("helper")
        assert len(refs) == 1
        assert refs[0].enclosing_scope == "Greeter.greet"

    def test_unknown_name_returns_empty(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _call("1", "do_work"))
        idx.build()
        assert idx.find_references("nonexistent") == []

    def test_references_raises_before_build(self):
        idx = SemanticIndex()
        with pytest.raises(RuntimeError, match="Call build\\(\\) first"):
            idx.find_references("foo")

    def test_references_property_raises_before_build(self):
        idx = SemanticIndex()
        with pytest.raises(RuntimeError, match="Call build\\(\\) first"):
            _ = idx.references

    def test_references_property_returns_dict(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _call("1", "foo"))
        idx.build()
        assert isinstance(idx.references, dict)
        assert "foo" in idx.references

    def test_resolve_false_returns_raw(self):
        """resolve=False (default) leaves resolved_definition as None."""
        # fn contains a self-referencing call to do_work inside its body
        fn = _fn("1", "do_work", children=[_call("2", "do_work")])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", fn)
        idx.build()
        refs = idx.find_references("do_work", resolve=False)
        assert all(r.resolved_definition is None for r in refs)

    @pytest.mark.parametrize(
        ("language", "node_type", "label"),
        [
            ("swift", "function_call_expression", "print"),
            ("php", "function_call_expression", "greet"),
            ("php", "member_call_expression", "run"),
            ("zig", "builtin_call_expression", "@import"),
            ("delphi", "exprCall", "WriteLn"),
            ("powershell", "command", "Write-Host"),
        ],
    )
    def test_newer_language_call_node_types_are_indexed(
        self,
        language: str,
        node_type: str,
        label: str,
    ):
        idx = _index_for(_node("1", node_type, label), language=language)
        refs = idx.find_references(label)
        assert len(refs) == 1
        assert refs[0].reference_kind == ReferenceKind.CALL
        assert refs[0].language == language

    def test_newer_language_definition_scope_is_used_for_references(self):
        tree = _node("1", "defProc", "Greet", [_node("2", "exprCall", "WriteLn")])
        idx = _index_for(tree, language="delphi")
        refs = idx.find_references("WriteLn")
        assert len(refs) == 1
        assert refs[0].enclosing_scope == "Greet"

    def test_clojure_list_calls_and_import_forms_are_indexed_as_references(self):
        tree = _node(
            "0",
            "source",
            "source",
            [
                _node(
                    "0.0",
                    "list_lit",
                    "greet",
                    [_leaf("0.0.0", "sym_lit", "defn"), _leaf("0.0.1", "sym_lit", "greet")],
                ),
                _node(
                    "0.1",
                    "list_lit",
                    "println",
                    [_leaf("0.1.0", "sym_lit", "println")],
                ),
                _node(
                    "0.2",
                    "list_lit",
                    "clojure.string",
                    [_leaf("0.2.0", "sym_lit", "require")],
                ),
            ],
        )
        idx = _index_for(tree, language="clojure")
        assert idx.find_references("greet") == []
        assert idx.find_references("println")[0].reference_kind == ReferenceKind.CALL
        assert (
            idx.find_references("clojure.string")[0].reference_kind
            == ReferenceKind.IMPORT
        )

    def test_elixir_definition_calls_are_not_indexed_as_call_references(self):
        tree = _node(
            "0",
            "source",
            "source",
            [
                _node(
                    "0.0",
                    "call",
                    "greet",
                    [_leaf("0.0.0", "identifier", "def")],
                ),
                _node("0.1", "call", "IO.puts", [_leaf("0.1.0", "identifier", "IO")]),
            ],
        )
        idx = _index_for(tree, language="elixir")
        assert idx.find_references("greet") == []
        assert idx.find_references("IO.puts")[0].reference_kind == ReferenceKind.CALL

    def test_end_to_end_newer_language_examples_populate_index(self):
        from intentumdiff import SemanticDiffer

        differ = SemanticDiffer()
        cases = {
            "powershell": ("code.ps1", ["Greet", "Add-Numbers"], ["Write-Host"]),
            "delphi": ("code.pas", ["Greet", "Add"], ["WriteLn"]),
            "clojure": ("code.clj", ["greet", "add"], ["println"]),
            "elixir": ("code.ex", ["Greeter", "Greeter.greet"], []),
            "haskell": ("code.hs", ["greet", "add"], []),
        }
        for language, (filename, symbols, references) in cases.items():
            example = differ.playground_example(language)
            assert example is not None
            tree, detected = differ.parse(example["new"], filename, language)
            idx = _index_for(tree, language=detected, filename=filename)
            for symbol in symbols:
                assert symbol in idx.symbols
            for reference in references:
                assert idx.find_references(reference)

    def test_resolve_true_single_match(self):
        """resolve=True populates resolved_definition when exactly one definition."""
        # fn defines do_work and contains a recursive call to itself
        fn = _fn("1", "do_work", children=[_call("2", "do_work")])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", fn)
        idx.build()
        refs = idx.find_references("do_work", resolve=True)
        assert len(refs) == 1
        assert refs[0].resolved_definition is not None
        assert refs[0].resolved_definition.qualified_name == "do_work"

    def test_resolve_true_ambiguous_leaves_none(self):
        """resolve=True leaves resolved_definition=None when multiple definitions match."""
        # Two files each define 'do_work'; file b also calls it.
        fn_a = _fn("1", "do_work")
        fn_b = _fn("3", "do_work", children=[_call("4", "do_work")])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", fn_a)
        idx.add_tree("b.py", "python", fn_b)
        idx.build()
        refs = idx.find_references("do_work", resolve=True)
        assert all(r.resolved_definition is None for r in refs)


class TestToFilesJson:
    def test_serialisation_structure(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "foo"))
        payload = json.loads(idx.to_files_json())
        assert isinstance(payload, list)
        assert payload[0]["filename"] == "a.py"
        assert payload[0]["language"] == "python"
        assert "tree" in payload[0]


class TestLoadSymbolTableJson:
    def test_load_overrides_python_symbols(self):
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", _fn("1", "foo"))
        idx.build()

        # Simulate Wasm result overriding the symbol table
        wasm_result = {
            "bar": [
                {
                    "qualified_name": "bar",
                    "file": "b.py",
                    "node_type": "function_definition",
                    "node_id": "99",
                    "start_line": 0,
                    "start_col": 0,
                    "end_line": 5,
                    "end_col": 0,
                    "language": "python",
                }
            ]
        }
        idx.load_symbol_table_json(json.dumps(wasm_result))
        assert "bar" in idx.symbols
        assert "foo" not in idx.symbols

    def test_load_marks_built(self):
        idx = SemanticIndex()
        idx.load_symbol_table_json("{}")
        assert idx._built is True


class TestLoadReferenceTableJson:
    def test_load_overrides_python_references(self):
        """load_reference_table_json replaces the Python-built _references."""
        tree = _node("0", "module", "module", children=[_call("1", "do_work")])
        idx = SemanticIndex()
        idx.add_tree("a.py", "python", tree)
        idx.build()
        # Confirm Python path extracted a reference.
        assert len(idx.find_references("do_work")) == 1

        wasm_ref_result = {
            "external_fn": [
                {
                    "qualified_name": "external_fn",
                    "file": "b.py",
                    "node_id": "5",
                    "reference_kind": "CALL",
                    "position": {"start_line": 0, "start_col": 0, "end_line": 0, "end_col": 10},
                    "language": "python",
                    "enclosing_scope": None,
                    "resolved_definition": None,
                }
            ]
        }
        idx.load_reference_table_json(json.dumps(wasm_ref_result))
        # Wasm result replaces the Python result.
        assert idx.find_references("do_work") == []
        assert len(idx.find_references("external_fn")) == 1
        assert idx.find_references("external_fn")[0].reference_kind == ReferenceKind.CALL
