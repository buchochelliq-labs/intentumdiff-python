"""Release guard: deleted Python semantic mirrors must not reappear."""

import ast
from pathlib import Path


_REMOVED = {
    "_all_semantic_nodes",
    "_changes_to_stream_events",
    "_semantic_parent_map",
    "_annotate_text_diffs",
    "_is_named_entity_node",
    "_surface_changed_in_place_entities",
    "_clean_string_literal_label",
    "_slice_source_text",
}


def test_retired_python_semantic_presentation_helpers_stay_absent():
    package = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"
    presentation = ast.parse((package / "_differ_presentation.py").read_text(encoding="utf-8"))
    differ = ast.parse((package / "differ.py").read_text(encoding="utf-8"))
    functions = {node.name for node in presentation.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not (_REMOVED & functions)
    assert "_NAMED_ENTITY_NODE_TYPES" not in {n.id for n in ast.walk(presentation) if isinstance(n, ast.Name)}
    assert "_GENERIC_STRING_LABELS" not in {n.id for n in ast.walk(presentation) if isinstance(n, ast.Name)}
    for node in ast.walk(differ):
        if isinstance(node, ast.ImportFrom) and node.module == "intentumdiff._differ_presentation":
            assert not (_REMOVED & {alias.name for alias in node.names})


def test_presentation_import_contract():
    """Catch missing Rust-backed adapters before a wheel is ever built."""
    package = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"
    presentation = ast.parse(
        (package / "_differ_presentation.py").read_text(encoding="utf-8")
    )
    differ = ast.parse((package / "differ.py").read_text(encoding="utf-8"))
    available = {
        node.name
        for node in presentation.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported = {
        alias.name
        for node in ast.walk(differ)
        if isinstance(node, ast.ImportFrom)
        and node.module == "intentumdiff._differ_presentation"
        for alias in node.names
    }
    assert imported <= available, f"Missing presentation exports: {sorted(imported - available)}"


_RETIRED_RUST_OPTIONAL_ADAPTERS = {
    "try_rust_core_diff",
    "try_rust_core_cst_diff",
    "try_rust_core_tree_diff",
    "try_rust_core_sources_stage11",
}
_RETIRED_RUST_OPTIONAL_DTOS = {
    "RustCoreAttempt",
    "RustCoreTreeAttempt",
    "RustCoreSourceAttempt",
}
_RETIRED_PYTHON_MATCHING_RECONSTRUCTION = {
    "_semantic_node_lookup",
    "_matching_from_payload",
    "_changes_from_payload",
}


def test_obsolete_optional_rust_adapters_and_python_matching_mirrors_stay_absent():
    """The binding must never revive retired fail-open Python stage adapters."""
    package = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"
    module = ast.parse((package / "rust_core.py").read_text(encoding="utf-8"))
    definitions = {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    forbidden = (
        _RETIRED_RUST_OPTIONAL_ADAPTERS
        | _RETIRED_RUST_OPTIONAL_DTOS
        | _RETIRED_PYTHON_MATCHING_RECONSTRUCTION
    )
    assert not (definitions & forbidden), (
        f"Obsolete Rust adapters: {sorted(definitions & forbidden)}"
    )


def test_python_style_shortcut_is_retired_even_from_unreachable_paths():
    """Only Rust may decide formatting equivalence or discard source edits."""
    package = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"
    differ = ast.parse((package / "differ.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "SemanticDiff"
        and node.func.attr == "style_only"
        for node in ast.walk(differ)
    ), "Python must not fabricate SemanticDiff.style_only for filtered-CST equality"
    assert not any(
        isinstance(node, ast.Name) and node.id in {
            "build_style_only_evidence", "_compute_structural_hash_for_tree",
        }
        for node in ast.walk(differ)
    ), "Python pipeline must not reconstruct discarded style edits from CST hashes"
