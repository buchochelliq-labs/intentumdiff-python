"""Ownership ratchet: retired semantic definitions may not come back to the binding.

Each entry below is a table, constant or helper that encoded a product decision core owns
(0.0.3 thin-binding issues #296-#303). They were removed from ``src/intentumdiff``; core is
now the single owner. The check walks every shipped module's AST, so re-adding one of these
definitions anywhere in the package (under any module) fails, as does a new module-level
built-in schema URL table.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"

# Retired definition -> the core surface that owns the decision.
_RETIRED = {
    # #297: schema identity and the built-in provider URL catalogue (core schema_profiles).
    "IDENTITY_FIELD_CANDIDATES": "schema_profiles derive/provider",
    "DBT_IDENTITY_FIELDS": "schema_profiles provider",
    "DBT_SCHEMA_BASE": "schema_profiles provider",
    # #298: built-in provider-root catalogue (core schema_profiles validate).
    "BUILTIN_PROVIDER_ROOTS": "schema_profiles validate",
    # #301: invariance evaluator / language / status admissibility (core invariance catalogue).
    "_ALLOWED_EVALUATORS": "core invariances/rules.yaml",
    "_KNOWN_LANGUAGE_IDS": "core invariances/rules.yaml",
    "_known_evaluator": "core invariances/rules.yaml",
    "_known_languages": "core invariances/rules.yaml",
    "_status_consistency": "core invariances/rules.yaml",
    # #302: Python-manufactured style-only verdict (core finalize_review / review_text).
    "SemanticDiff.style_only": "core-certified SemanticDiff",
    # #303: file rename/move lifecycle classified from path names (core lifecycle facts).
    "SemanticDiff.file_renamed": "core lifecycle facts",
    "SemanticDiff.file_moved": "core lifecycle facts",
    # #296: the DTO's own copy of the guardrail gate, which overwrote the supplied verdict.
    "GuardrailCheckResult._derive_counts": "core guardrail_check_result (pending)",
}

_RETIRED_SUFFIXES = ("_SCHEMA_URL", "_SCHEMA_URLS")


def _defined_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}

    def add(name: str, module: str) -> None:
        found.setdefault(name, []).append(module)

    for path in sorted(_PACKAGE.rglob("*.py")):
        module = path.relative_to(_PACKAGE).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add(node.name, module)
            elif isinstance(node, ast.ClassDef):
                add(node.name, module)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add(f"{node.name}.{item.name}", module)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        add(target.id, module)
    return found


def test_retired_semantic_definitions_stay_out_of_the_binding() -> None:
    names = _defined_names()
    back = {name: names[name] for name in _RETIRED if name in names}
    assert not back, (
        "These definitions encode decisions owned by intentumdiff-core and were retired from "
        f"the binding; consume the core result instead: {back}"
    )


def test_no_builtin_schema_url_tables_in_the_binding() -> None:
    tables = {
        name: modules
        for name, modules in _defined_names().items()
        if name.isupper() and name.endswith(_RETIRED_SUFFIXES)
    }
    assert not tables, (
        "Built-in schema URL tables belong to core's schema_profiles catalogue (#297): "
        f"{tables}"
    )
