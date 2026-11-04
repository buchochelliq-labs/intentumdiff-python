"""Rust-owned LSP diagnostics output, structured into Python transport DTOs.

Semantic classification and UTF-8 byte→UTF-16 ranges belong to the Rust core.
The source-aware C ABI refuses invalid spans rather than publishing displaced
editor annotations. Python performs no diagnostic semantic classification.
"""
from __future__ import annotations

from lsprotocol import types as lsp_types
from lsprotocol.converters import get_converter

from intentumdiff.core.models import SemanticDiff, SemanticNode
from intentumdiff.rust_core import _c_abi_call


def node_to_lsp_range(node: SemanticNode, source: str) -> lsp_types.Range:
    """Marshal a Rust source-judged UTF-16 range, requiring the actual source."""
    value = _c_abi_call("lsp_utf16_range", node.model_dump_json(), source)
    if not isinstance(value, dict):
        raise ValueError("Rust LSP range must be an object")
    return get_converter().structure(value, lsp_types.Range)


def semantic_diff_to_diagnostics(
    diff: SemanticDiff, *, new_source: str,
) -> list[lsp_types.Diagnostic]:
    """Decode Rust's source-aware diagnostic inventory (no Python fallback)."""
    value = _c_abi_call(
        "lsp_server_diagnostics_utf16", diff.model_dump_json(), new_source,
    )
    if not isinstance(value, list):
        raise ValueError("Rust LSP diagnostics must be an array")
    converter = get_converter()
    return [converter.structure(row, lsp_types.Diagnostic) for row in value]
