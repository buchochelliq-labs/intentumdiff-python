"""
intentdiff.analysis.cross_file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Detect cross-file semantic changes by comparing two ``SemanticIndex``
snapshots.

Rust-authoritative (#91): the detection algorithm — ``MOVE_TO_MODULE`` (a
definition appears in a different file under the same qualified name),
``SPLIT_MODULE`` (one file's symbols fan out across several new files), and
``CROSS_FILE_RENAME`` (a same-typed, same-scope definition reappears under a new
name) — runs in the Rust core (``index-engine-lib`` via ``diff_symbol_tables_json``).
The former pure-Python mirror was deleted; only the thin DTO-marshalling wrapper
survives here, so "if Python didn't exist, IntentDiff still works".
"""

from __future__ import annotations

import json

from intentdiff.core.index import SemanticIndex
from intentdiff.core.models import ChangeType, CrossFileChange


def detect_cross_file_changes(
    old_index: SemanticIndex,
    new_index: SemanticIndex,
) -> list[CrossFileChange]:
    """
    Compare *old_index* and *new_index* via the Rust core and return the
    resulting ``CrossFileChange`` instances.

    Both indices must have had ``build()`` called first. The comparison itself
    is performed by the Rust core over the flat symbol tables; this function only
    marshals the tables in and the change list back out. Returns an empty list
    when the core is unavailable.
    """
    from intentdiff.rust_core import try_rust_diff_symbol_tables

    old_json = json.dumps(
        {k: [d.model_dump() for d in defs] for k, defs in old_index.symbols.items()}
    )
    new_json = json.dumps(
        {k: [d.model_dump() for d in defs] for k, defs in new_index.symbols.items()}
    )

    result = try_rust_diff_symbol_tables(old_json, new_json)
    if result is None:
        return []

    changes: list[CrossFileChange] = []
    for item in result:
        changes.append(
            CrossFileChange(
                change_type=ChangeType(item["change_type"]),
                symbol_name=item["symbol_name"],
                old_file=item["old_file"],
                new_file=item["new_file"],
                old_node_id=item.get("old_node_id"),
                new_node_id=item.get("new_node_id"),
                old_position=item.get("old_position"),
                new_position=item.get("new_position"),
                old_language=item.get("old_language", ""),
                new_language=item.get("new_language", ""),
                node_type=item.get("node_type", ""),
                symbol_kind=item.get("symbol_kind", ""),
                confidence=item.get("confidence", 1.0),
                description=item.get("description", ""),
            )
        )
    return changes
