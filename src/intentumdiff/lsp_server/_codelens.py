"""Rust-owned LSP CodeLens presentation, with correct source-side UTF-16 spans.

Rust owns which changes become lenses, their titles, and byte→UTF-16
coordinates. This thin Python module only converts Rust JSON to lsprotocol DTOs.
"""
from __future__ import annotations

from lsprotocol import types as lsp_types
from lsprotocol.converters import get_converter

from intentumdiff.core.models import SemanticDiff, SemanticNode
from intentumdiff.rust_core import _c_abi_call


def node_to_lsp_range(node: SemanticNode, source: str) -> lsp_types.Range:
    """Convert only through the source-aware Rust C ABI, never guess columns."""
    value = _c_abi_call("lsp_utf16_range", node.model_dump_json(), source)
    if not isinstance(value, dict):
        raise ValueError("Rust LSP range must be an object")
    return get_converter().structure(value, lsp_types.Range)


def semantic_diff_to_codelens(
    diff: SemanticDiff, *, old_source: str, new_source: str,
) -> list[lsp_types.CodeLens]:
    """Decode Rust's source-anchored CodeLens inventory without Python policy."""
    value = _c_abi_call(
        "lsp_server_codelens_utf16", diff.model_dump_json(),
        old_source, new_source,
    )
    if not isinstance(value, list):
        raise ValueError("Rust LSP CodeLens must be an array")
    converter = get_converter()
    return [converter.structure(row, lsp_types.CodeLens) for row in value]
