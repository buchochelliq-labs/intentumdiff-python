"""Release guard: deleted Python semantic mirrors must not reappear."""

import ast
from pathlib import Path


_REMOVED = {
    "_all_semantic_nodes",
    "_semantic_parent_map",
    "_annotate_text_diffs",
    "_is_named_entity_node",
    "_surface_changed_in_place_entities",
    "_clean_string_literal_label",
}


def test_retired_python_semantic_presentation_helpers_stay_absent():
    package = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"
    presentation = ast.parse((package / "_differ_presentation.py").read_text(encoding="utf-8"))
    differ = ast.parse((package / "differ.py").read_text(encoding="utf-8"))
    functions = {node.name for node in presentation.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not (_REMOVED & functions)
    for node in ast.walk(differ):
        if isinstance(node, ast.ImportFrom) and node.module == "intentumdiff._differ_presentation":
            assert not (_REMOVED & {alias.name for alias in node.names})
