"""
intentdiff.core.index
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``SemanticIndex`` — a per-commit, multi-file symbol table.

Each ``SemanticIndex`` holds the ``SemanticNode`` trees for every file in a
repository snapshot (or any other collection of files).  It provides:

* ``add_tree(filename, language, tree)``  — register one file's parsed tree.
* ``build()`` — extract the flat symbol table and reference table.
* ``find_definition(name)`` — look up a symbol by qualified name.
* ``find_references(name, resolve=False)`` — return call-site / import usages.
  Pass ``resolve=True`` to attach the matching ``SymbolDefinition`` when
  exactly one definition is found.

The heavy lifting (symbol-table + reference-table extraction) is delegated to
the Rust core (``index-engine-lib``, via the native ``build_symbol_table_json``
/ ``build_reference_table_json`` entrypoints). Rust is authoritative — the
former pure-Python extraction mirror was deleted (#91); without the core the
tables are simply empty ("if Python didn't exist, the engine still lives in
Rust"). Cross-file diffing lives in ``intentdiff.analysis.cross_file``, also a
thin wrapper over the same Rust core.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from intentdiff.core.models import ReferenceUsage, SymbolDefinition

if TYPE_CHECKING:
    from intentdiff.core.models import SemanticNode

class SemanticIndex:
    """
    Multi-file symbol table for a single repository snapshot.

    Usage::

        differ = SemanticDiffer(config)
        old_index = SemanticIndex()
        for filename, source in old_files.items():
            diff = differ._parse(source, filename)   # internal helper
            old_index.add_tree(filename, diff.language, diff.tree)
        old_index.build()

        # Then compare two snapshots with
        # intentdiff.analysis.cross_file.detect_cross_file_changes(old, new).
    """

    def __init__(self) -> None:
        # Raw input: list of (filename, language, tree) triples
        self._files: list[tuple[str, str, "SemanticNode"]] = []
        # Symbol table built by build()
        self._symbols: dict[str, list[SymbolDefinition]] = {}
        # Reference table built by build()
        self._references: dict[str, list[ReferenceUsage]] = {}
        self._built: bool = False

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def add_tree(self, filename: str, language: str, tree: "SemanticNode") -> None:
        """Register a parsed semantic tree for *filename*."""
        if self._built:
            raise RuntimeError(
                "SemanticIndex is already built; create a new instance to add more trees."
            )
        self._files.append((filename, language, tree))

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> "SemanticIndex":
        """Build the symbol + reference tables via the Rust core (index-engine-lib).

        Rust-authoritative (#91): extraction runs in the native
        ``build_symbol_table_json`` / ``build_reference_table_json`` entrypoints
        (the same code the index-engine Wasm plugin wraps). The former Python
        extraction mirror was deleted. When the core is unavailable the tables
        stay empty rather than falling back — "if Python didn't exist, the
        engine still lives in Rust".
        """
        from intentdiff.rust_core import (
            try_rust_build_reference_table,
            try_rust_build_symbol_table,
        )

        self._symbols = {}
        self._references = {}
        files_json = self.to_files_json()
        symbol_json = try_rust_build_symbol_table(files_json)
        if symbol_json is not None:
            self.load_symbol_table_json(symbol_json)
        reference_json = try_rust_build_reference_table(files_json)
        if reference_json is not None:
            self.load_reference_table_json(reference_json)
        self._built = True
        return self

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    @property
    def symbols(self) -> dict[str, list[SymbolDefinition]]:
        """Return the flat symbol table (qualified-name → definitions)."""
        if not self._built:
            raise RuntimeError("Call build() first.")
        return self._symbols

    @property
    def references(self) -> dict[str, list[ReferenceUsage]]:
        """Return the reference table (label → usage sites)."""
        if not self._built:
            raise RuntimeError("Call build() first.")
        return self._references

    def find_definition(self, name: str) -> list[SymbolDefinition]:
        """Return all definitions matching *name* (exact qualified-name lookup)."""
        if not self._built:
            raise RuntimeError("Call build() first.")
        return self._symbols.get(name, [])

    def find_references(self, name: str, resolve: bool = False) -> list[ReferenceUsage]:
        """
        Return call-site / import usages of *name*.

        Parameters
        ----------
        name:
            The label to search for (e.g. ``"do_work"`` or ``"MyClass"``).
        resolve:
            When ``True``, populate ``ReferenceUsage.resolved_definition`` for
            each result if exactly one matching ``SymbolDefinition`` is found
            in this index.  Defaults to ``False`` (raw usages only).
        """
        if not self._built:
            raise RuntimeError("Call build() first.")
        refs = self._references.get(name, [])
        if not resolve:
            return refs
        # Attach resolved definition when the match is unambiguous.
        defs = self.find_definition(name)
        resolved = defs[0] if len(defs) == 1 else None
        return [
            ref.model_copy(update={"resolved_definition": resolved}) for ref in refs
        ]

    # ------------------------------------------------------------------
    # Serialisation helpers (feed the native index-engine primitives)
    # ------------------------------------------------------------------

    def to_files_json(self) -> str:
        """Serialise the registered trees to the JSON format expected by the
        Rust core's ``build_symbol_table_json`` / ``build_reference_table_json``
        entrypoints (a JSON array of ``{filename, language, tree}`` entries)."""
        payload = [
            {"filename": filename, "language": language, "tree": json.loads(tree.model_dump_json())}
            for filename, language, tree in self._files
        ]
        return json.dumps(payload)

    def load_symbol_table_json(self, json_str: str) -> None:
        """
        Populate ``_symbols`` from a SymbolTable JSON returned by the Rust core's
        ``build_symbol_table_json`` entrypoint.
        """
        raw: dict[str, list[dict]] = json.loads(json_str)
        self._symbols = {
            qname: [SymbolDefinition(**entry) for entry in entries]
            for qname, entries in raw.items()
        }
        self._built = True

    def load_reference_table_json(self, json_str: str) -> None:
        """
        Populate ``_references`` from a ReferenceTable JSON returned by the Rust
        core's ``build_reference_table_json`` entrypoint.
        """
        raw: dict[str, list[dict]] = json.loads(json_str)
        self._references = {
            label: [ReferenceUsage(**entry) for entry in entries]
            for label, entries in raw.items()
        }
