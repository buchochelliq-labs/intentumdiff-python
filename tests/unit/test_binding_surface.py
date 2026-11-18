"""Architecture gate for the thin-binding rule (issue #338).

This package may contain only the mechanics needed to expose intentumdiff-core: C-ABI
transport, DTO marshalling and error translation (see AGENTS.md and
.claude/skills/intentumdiff-python-boundary). The product behaviour that still ships here is
inventoried in ``binding_inventory.json`` with the core surface it migrates to.

The gate only lets that behaviour shrink:

- every shipped module must be classified, so a new module cannot land unreviewed;
- a new module may be binding, DTO or packaging, never behaviour;
- a behaviour module may not grow past its recorded ``max_lines``.

When a slice migrates to core, lower ``max_lines`` (or delete the entry with the module) in
the same change. Raising a budget is a designer decision, not a test fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE = _ROOT / "src" / "intentumdiff"
_INVENTORY = json.loads((Path(__file__).with_name("binding_inventory.json")).read_text("utf-8"))
_MODULES: dict[str, dict] = _INVENTORY["modules"]
_CATEGORIES = {"binding", "dto", "packaging", "behavior"}


def _shipped_modules() -> list[str]:
    return sorted(
        path.relative_to(_PACKAGE).as_posix()
        for path in _PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _lines(module: str) -> int:
    return len((_PACKAGE / module).read_text(encoding="utf-8").splitlines())


def test_every_shipped_module_is_classified() -> None:
    shipped = set(_shipped_modules())
    unclassified = sorted(shipped - set(_MODULES))
    assert not unclassified, (
        "New Python modules must be classified in tests/unit/binding_inventory.json. Only "
        "binding, dto or packaging modules may be added; behaviour belongs in "
        f"intentumdiff-core: {unclassified}"
    )
    removed = sorted(set(_MODULES) - shipped)
    assert not removed, f"Delete inventory entries for removed modules: {removed}"


def test_inventory_categories_are_known() -> None:
    bad = {m: e.get("category") for m, e in _MODULES.items() if e.get("category") not in _CATEGORIES}
    assert not bad, f"unknown categories: {bad}"
    missing_target = sorted(m for m, e in _MODULES.items() if not e.get("target"))
    assert not missing_target, f"every module must name its target/owner: {missing_target}"


def test_no_new_behavior_modules() -> None:
    behavior = sorted(m for m, e in _MODULES.items() if e["category"] == "behavior")
    assert len(behavior) <= _INVENTORY["behavior_module_budget"], (
        "A behaviour module was added. Product behaviour belongs in intentumdiff-core; "
        f"behaviour modules now: {len(behavior)}, budget: {_INVENTORY['behavior_module_budget']}"
    )


@pytest.mark.parametrize(
    "module", sorted(m for m, e in _MODULES.items() if e["category"] == "behavior")
)
def test_behavior_modules_only_shrink(module: str) -> None:
    budget = _MODULES[module]["max_lines"]
    actual = _lines(module)
    assert actual <= budget, (
        f"{module} grew from {budget} to {actual} lines. Behaviour must move to "
        f"intentumdiff-core ({_MODULES[module]['target']}), not grow here."
    )


def test_total_behavior_budget_is_consistent() -> None:
    total = sum(e["max_lines"] for e in _MODULES.values() if e["category"] == "behavior")
    assert total <= _INVENTORY["behavior_line_budget"], (
        "The per-module max_lines sum exceeds behavior_line_budget; budgets only go down."
    )
