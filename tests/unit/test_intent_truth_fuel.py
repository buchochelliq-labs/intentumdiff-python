"""Release-blocking intent and fuel regressions for competitor closure."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import ChangeGroupKind, ChangeType, DiffConfig
from intentumdiff.plugins.exceptions import PluginFuelExhausted
from pathlib import Path

pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "crates" / "parsers").exists(),
    reason="monorepo crates tree not present (#82 split python repo)",
)


@dataclass(frozen=True)
class IntentScenario:
    id: str
    language: str
    filename: str
    old: str
    new: str
    added_labels: frozenset[str] = frozenset()
    moved_labels: frozenset[str] = frozenset()
    modified_labels: frozenset[str] = frozenset()
    refactored_labels: frozenset[str] = frozenset()
    disallowed_move_labels: frozenset[str] = frozenset()
    disallowed_added_labels: frozenset[str] = frozenset()
    disallowed_deleted_labels: frozenset[str] = frozenset()


def _labels_for_type(diff, change_type: ChangeType) -> set[str]:
    labels: set[str] = set()
    for change in diff.changes:
        if _enum_value(change.change_type) != change_type.value:
            continue
        for node in (change.old_node, change.new_node):
            if node is not None:
                labels.update(_node_labels(node))
    return labels


def _group_labels(diff, kind: ChangeGroupKind) -> set[str]:
    labels: set[str] = set()
    for group in diff.change_groups:
        if _enum_value(group.kind) == kind.value:
            labels.update(group.old_labels)
            labels.update(group.new_labels)
    return labels


def _all_move_labels(diff) -> set[str]:
    return _labels_for_type(diff, ChangeType.MOVE) | _group_labels(
        diff,
        ChangeGroupKind.MOVED_CODE,
    )


def _all_refactoring_labels(diff) -> set[str]:
    return _labels_for_type(diff, ChangeType.REFACTORING) | _group_labels(
        diff,
        ChangeGroupKind.REFACTORING,
    )


def _enum_value(value) -> str:
    return getattr(value, "value", value)


def _node_labels(node) -> set[str]:
    return {node.label, *(label for child in node.children for label in _node_labels(child))}


TS_BASE = """\
export function parseOrder(input: string): number {
  return Number(input);
}

export function formatOrder(id: number): string {
  return `order-${id}`;
}
"""


POWERSHELL_BASE = """\
function Get-OrderId {
    param([string]$Input)
    return [int]$Input
}

function Format-OrderId {
    param([int]$Id)
    return "order-$Id"
}
"""


MARKDOWN_BASE = """\
# Release Checklist

## Build

- Run parser tests.

## Verify

- Inspect generated review.
"""


MDX_BASE = """\
# Release Checklist

<Step name="Build" status="ready" />

<Step name="Verify" status="pending" />
"""


INTENT_SCENARIOS = [
    IntentScenario(
        id="typescript-function-insert",
        language="typescript",
        filename="orders.ts",
        old=TS_BASE,
        new=TS_BASE
        + """
export function validateOrder(id: number): boolean {
  return id > 0;
}
""",
        added_labels=frozenset({"validateOrder"}),
        disallowed_move_labels=frozenset({"validateOrder"}),
    ),
    IntentScenario(
        id="typescript-class-method-insert",
        language="typescript",
        filename="service.ts",
        old="""\
export class OrderService {
  load(id: number): string {
    return `order-${id}`;
  }
}
""",
        new="""\
export class OrderService {
  load(id: number): string {
    return `order-${id}`;
  }

  validate(id: number): boolean {
    return id > 0;
  }
}
""",
        added_labels=frozenset({"validate"}),
        disallowed_move_labels=frozenset({"validate"}),
    ),
    IntentScenario(
        id="typescript-pure-move",
        language="typescript",
        filename="orders.ts",
        old=TS_BASE,
        new="""\
export function formatOrder(id: number): string {
  return `order-${id}`;
}

export function parseOrder(input: string): number {
  return Number(input);
}
""",
        moved_labels=frozenset({"formatOrder", "parseOrder"}),
    ),
    IntentScenario(
        id="typescript-move-plus-edit",
        language="typescript",
        filename="orders.ts",
        old=TS_BASE,
        new="""\
export function formatOrder(id: number): string {
  return `ORD-${id}`;
}

export function parseOrder(input: string): number {
  return Number(input);
}
""",
        moved_labels=frozenset({"formatOrder"}),
        modified_labels=frozenset({"formatOrder"}),
    ),
    IntentScenario(
        id="typescript-copy-paste-addition",
        language="typescript",
        filename="orders.ts",
        old=TS_BASE,
        new=TS_BASE
        + """
export function formatOrderForDisplay(id: number): string {
  return `order-${id}`;
}
""",
        added_labels=frozenset({"formatOrderForDisplay"}),
        disallowed_move_labels=frozenset({"formatOrderForDisplay"}),
    ),
    IntentScenario(
        id="typescript-function-rename",
        language="typescript",
        filename="orders.ts",
        old=TS_BASE,
        new=TS_BASE.replace("formatOrder", "formatOrderLabel"),
        # The routed engine (issue #57) presents a rename as a REFACTORING pair — a
        # richer contract than the retired python pipeline's rename-as-MODIFICATION.
        # The truth requirement is unchanged: the rename SURFACES under both names and
        # never leaks as ADDITION/DELETION.
        refactored_labels=frozenset({"formatOrder", "formatOrderLabel"}),
        disallowed_added_labels=frozenset({"formatOrderLabel"}),
        disallowed_deleted_labels=frozenset({"formatOrder"}),
    ),
    IntentScenario(
        id="typescript-duplicate-helper-plus-edited-original",
        language="typescript",
        filename="orders.ts",
        old=TS_BASE,
        new="""\
export function parseOrder(input: string): number {
  return Number.parseInt(input, 10);
}

export function formatOrder(id: number): string {
  return `order-${id}`;
}

export function formatOrderCopy(id: number): string {
  return `order-${id}`;
}
""",
        added_labels=frozenset({"formatOrderCopy"}),
        modified_labels=frozenset({"parseOrder"}),
        disallowed_move_labels=frozenset({"formatOrderCopy"}),
    ),
    IntentScenario(
        id="typescript-nested-method-insert",
        language="typescript",
        filename="service.ts",
        old="""\
export class OrderService {
  private cache = new Map<number, string>();

  load(id: number): string {
    return this.cache.get(id) ?? `order-${id}`;
  }
}
""",
        new="""\
export class OrderService {
  private cache = new Map<number, string>();

  load(id: number): string {
    return this.cache.get(id) ?? `order-${id}`;
  }

  private remember(id: number, value: string): void {
    this.cache.set(id, value);
  }
}
""",
        added_labels=frozenset({"remember"}),
        disallowed_move_labels=frozenset({"remember"}),
    ),
    IntentScenario(
        id="typescript-adjacent-sibling-reorder",
        language="typescript",
        filename="orders.ts",
        old="""\
export function one(): number {
  return 1;
}

export function two(): number {
  return 2;
}

export function three(): number {
  return 3;
}
""",
        new="""\
export function two(): number {
  return 2;
}

export function one(): number {
  return 1;
}

export function three(): number {
  return 3;
}
""",
        moved_labels=frozenset({"one", "two"}),
    ),
    IntentScenario(
        id="powershell-function-insert",
        language="powershell",
        filename="orders.ps1",
        old=POWERSHELL_BASE,
        new=POWERSHELL_BASE
        + """
function Test-OrderId {
    param([int]$Id)
    return $Id -gt 0
}
""",
        added_labels=frozenset({"Test-OrderId"}),
        disallowed_move_labels=frozenset({"Test-OrderId"}),
    ),
    IntentScenario(
        id="powershell-pure-move",
        language="powershell",
        filename="orders.ps1",
        old=POWERSHELL_BASE,
        new="""\
function Format-OrderId {
    param([int]$Id)
    return "order-$Id"
}

function Get-OrderId {
    param([string]$Input)
    return [int]$Input
}
""",
        moved_labels=frozenset({"Format-OrderId", "Get-OrderId"}),
    ),
    IntentScenario(
        id="powershell-move-plus-edit",
        language="powershell",
        filename="orders.ps1",
        old=POWERSHELL_BASE,
        new="""\
function Format-OrderId {
    param([int]$Id)
    return "ORDER-$Id"
}

function Get-OrderId {
    param([string]$Input)
    return [int]$Input
}
        """,
        moved_labels=frozenset({"Format-OrderId"}),
        # Literal labels are source-exact including quotes since the #46 capture sweep.
        modified_labels=frozenset({'"ORDER-$Id"'}),
    ),
    IntentScenario(
        id="powershell-helper-insert-next-to-existing-helper",
        language="powershell",
        filename="orders.ps1",
        old=POWERSHELL_BASE,
        new=POWERSHELL_BASE
        + """
function Format-OrderLabel {
    param([int]$Id)
    return "order-$Id"
}
""",
        added_labels=frozenset({"Format-OrderLabel"}),
        disallowed_move_labels=frozenset({"Format-OrderLabel"}),
    ),
    IntentScenario(
        id="powershell-function-rename",
        language="powershell",
        filename="orders.ps1",
        old=POWERSHELL_BASE,
        new=POWERSHELL_BASE.replace("Format-OrderId", "Format-OrderLabel"),
        # Same rename-as-REFACTORING contract as typescript-function-rename above.
        refactored_labels=frozenset({"Format-OrderId", "Format-OrderLabel"}),
        disallowed_added_labels=frozenset({"Format-OrderLabel"}),
        disallowed_deleted_labels=frozenset({"Format-OrderId"}),
    ),
    IntentScenario(
        id="powershell-duplicate-helper-plus-edited-original",
        language="powershell",
        filename="orders.ps1",
        old=POWERSHELL_BASE,
        new="""\
function Get-OrderId {
    param([string]$Input)
    return [int]$Input.Trim()
}

function Format-OrderId {
    param([int]$Id)
    return "order-$Id"
}

function Format-OrderCopy {
    param([int]$Id)
    return "order-$Id"
}
""",
        added_labels=frozenset({"Format-OrderCopy"}),
        modified_labels=frozenset({"Get-OrderId"}),
        disallowed_move_labels=frozenset({"Format-OrderCopy"}),
    ),
    IntentScenario(
        id="powershell-adjacent-sibling-reorder",
        language="powershell",
        filename="orders.ps1",
        old="""\
function Invoke-One {
    return 1
}

function Invoke-Two {
    return 2
}

function Invoke-Three {
    return 3
}
""",
        new="""\
function Invoke-Two {
    return 2
}

function Invoke-One {
    return 1
}

function Invoke-Three {
    return 3
}
""",
        moved_labels=frozenset({"Invoke-One", "Invoke-Two"}),
    ),
    IntentScenario(
        id="markdown-section-insert",
        language="generic",
        filename="README.md",
        old=MARKDOWN_BASE,
        new=MARKDOWN_BASE.replace(
            "## Verify\n",
            "## Package\n\n- Build the VSIX and shell artifacts.\n\n## Verify\n",
        ),
        added_labels=frozenset({"## Package"}),
        disallowed_move_labels=frozenset({"## Package"}),
    ),
    IntentScenario(
        id="markdown-section-move",
        language="generic",
        filename="README.md",
        old=MARKDOWN_BASE,
        new="""\
# Release Checklist

## Verify

- Inspect generated review.

## Build

- Run parser tests.
""",
        moved_labels=frozenset({"## Verify", "## Build"}),
    ),
    IntentScenario(
        id="markdown-fenced-code-edit",
        language="generic",
        filename="README.md",
        old=MARKDOWN_BASE
        + """
```ts
export const answer = 41;
```
""",
        new=MARKDOWN_BASE
        + """
```ts
export const answer = 42;
```
""",
        # One changed prose/code line = one line-level MODIFICATION (whole-line label, char
        # detail in text_diff) — not per-character text_span churn (the old "2" label). See
        # the intentumdiff-diff-expectations oracle and _generic_line_modification.
        modified_labels=frozenset({"export const answer = 42;"}),
    ),
    IntentScenario(
        id="markdown-heading-rename-not-section-move",
        language="generic",
        filename="README.md",
        old=MARKDOWN_BASE,
        new=MARKDOWN_BASE.replace("## Verify", "## Validate"),
        modified_labels=frozenset({"## Validate"}),
        disallowed_move_labels=frozenset({"## Verify", "## Validate"}),
        disallowed_added_labels=frozenset({"## Validate"}),
        disallowed_deleted_labels=frozenset({"## Verify"}),
    ),
    IntentScenario(
        id="mdx-component-insert",
        language="mdx",
        filename="checklist.mdx",
        old=MDX_BASE,
        new=MDX_BASE + '\n<Step name="Package" status="blocked" />\n',
        added_labels=frozenset({"Step Package"}),
        disallowed_move_labels=frozenset({"Step Package"}),
    ),
    IntentScenario(
        id="mdx-component-move",
        language="mdx",
        filename="checklist.mdx",
        old=MDX_BASE,
        new="""\
# Release Checklist

<Step name="Verify" status="pending" />

<Step name="Build" status="ready" />
""",
        moved_labels=frozenset({"Step Build", "Step Verify"}),
    ),
    IntentScenario(
        id="mdx-component-prop-edit",
        language="mdx",
        filename="checklist.mdx",
        old=MDX_BASE,
        new=MDX_BASE.replace('status="pending"', 'status="ready"'),
        modified_labels=frozenset({"Step Verify"}),
        disallowed_move_labels=frozenset({"Step Verify"}),
    ),
    IntentScenario(
        id="mdx-component-move-plus-prop-edit",
        language="mdx",
        filename="checklist.mdx",
        old=MDX_BASE,
        new="""\
# Release Checklist

<Step name="Verify" status="blocked" />

<Step name="Build" status="ready" />
""",
        moved_labels=frozenset({"Step Verify"}),
        modified_labels=frozenset({"Step Verify"}),
    ),
]


@pytest.mark.parametrize(
    "scenario",
    INTENT_SCENARIOS,
    ids=[scenario.id for scenario in INTENT_SCENARIOS],
)
def test_intent_truth_for_insert_move_and_move_edit_scenarios(
    scenario: IntentScenario,
) -> None:
    diff = SemanticDiffer(DiffConfig(diagnostics=True)).diff_strings(
        scenario.old,
        scenario.new,
        filename=scenario.filename,
        language_hint=scenario.language,
    )

    assert not diff.parse_errors
    assert not diff.is_style_only
    assert diff.changes or diff.change_groups

    added_labels = _labels_for_type(diff, ChangeType.ADDITION)
    move_labels = _all_move_labels(diff)
    modified_labels = _labels_for_type(diff, ChangeType.MODIFICATION)
    modified_labels |= _group_labels(diff, ChangeGroupKind.MEANINGFUL_CHANGE)
    refactored_labels = _all_refactoring_labels(diff)
    deleted_labels = _labels_for_type(diff, ChangeType.DELETION)

    assert scenario.added_labels <= added_labels
    assert scenario.moved_labels & move_labels or not scenario.moved_labels
    assert scenario.modified_labels & modified_labels or not scenario.modified_labels
    assert scenario.refactored_labels & refactored_labels or not scenario.refactored_labels
    assert not scenario.disallowed_move_labels & move_labels
    assert not scenario.disallowed_added_labels & added_labels
    assert not scenario.disallowed_deleted_labels & deleted_labels

    if scenario.disallowed_move_labels:
        # The point: the absence of disallowed moves must come from a pipeline that
        # actually CONSIDERED move promotion, not from an early exit. On the python
        # pipeline that evidence is the move_promotion stage events; on the routed
        # Rust path (issue #57) it is the finalize record (move recovery/promotion
        # runs inside finalize_review_json; per-pass trace arrives with issue #54).
        trace = diff.metadata["diagnostics"]
        move_events = [
            event
            for event in trace["events"]
            if event["stage"] in ("move_promotion", "finalize")
        ]
        assert move_events


def _large_typescript(count: int) -> str:
    return "\n".join(
        f"export function handler{i}(value: number): number {{ return value + {i}; }}"
        for i in range(count)
    ) + "\n"


def _large_powershell(count: int) -> str:
    return "\n".join(
        "\n".join(
            [
                f"function Invoke-Handler{i} {{",
                "    param([int]$Value)",
                f"    return $Value + {i}",
                "}",
            ]
        )
        for i in range(count)
    ) + "\n"


def _large_markdown(count: int) -> str:
    return "\n".join(
        f"## Section {i}\n\n- Run check {i}.\n" for i in range(count)
    )


def _large_mdx(count: int) -> str:
    return "# Matrix\n\n" + "\n".join(
        f'<Check name="case-{i}" status="ready" />' for i in range(count)
    ) + "\n"


@pytest.mark.parametrize(
    ("language", "filename", "old", "new", "expects_wasm_telemetry"),
    [
        (
            "typescript",
            "large.ts",
            _large_typescript(220),
            _large_typescript(220)
            + "export function addedHandler(value: number): number { return value * 2; }\n",
            True,
        ),
        (
            "powershell",
            "large.ps1",
            _large_powershell(180),
            _large_powershell(180)
            + "\n".join(
                [
                    "function Invoke-AddedHandler {",
                    "    param([int]$Value)",
                    "    return $Value * 2",
                    "}",
                    "",
                ]
            ),
            True,
        ),
        (
            "generic",
            "large.md",
            _large_markdown(220),
            _large_markdown(220) + "## Added Section\n\n- Confirm review output.\n",
            False,
        ),
        (
            "mdx",
            "large.mdx",
            _large_mdx(180),
            _large_mdx(180) + '<Check name="added" status="blocked" />\n',
            True,
        ),
    ],
    ids=["typescript-large", "powershell-large", "markdown-large", "mdx-large"],
)
def test_large_language_inputs_do_not_silently_bomb_or_blank(
    language: str,
    filename: str,
    old: str,
    new: str,
    expects_wasm_telemetry: bool,
) -> None:
    diff = SemanticDiffer(DiffConfig(diagnostics=True)).diff_strings(
        old,
        new,
        filename=filename,
        language_hint=language,
    )

    assert not any("FUEL_EXCEEDED" in error for error in diff.parse_errors)
    assert diff.changes or diff.change_groups
    assert diff.has_semantic_changes
    assert not diff.is_style_only

    telemetry = diff.metadata.get("engine_telemetry", {})
    process_calls = [
        call for call in telemetry.get("calls", []) if call["function"] == "process"
    ]
    if expects_wasm_telemetry:
        assert process_calls
        assert all(call["fuel_budget"] for call in process_calls)
        assert any((call["fuel_consumed"] or 0) > 0 for call in process_calls)


@pytest.mark.parametrize(
    ("language", "filename", "old", "new"),
    [
        (
            "typescript",
            "fuel.ts",
            _large_typescript(30),
            _large_typescript(30)
            + "export function tinyFuelAdded(value: number): number { return value; }\n",
        ),
        (
            "powershell",
            "fuel.ps1",
            _large_powershell(30),
            _large_powershell(30)
            + "function Invoke-TinyFuelAdded { param([int]$Value) return $Value }\n",
        ),
        (
            "mdx",
            "fuel.mdx",
            _large_mdx(30),
            _large_mdx(30) + '<Check name="tiny-fuel" status="blocked" />\n',
        ),
    ],
    ids=["typescript-tiny-fuel", "powershell-tiny-fuel", "mdx-tiny-fuel"],
)
def test_tiny_wasm_fuel_fails_explicitly_instead_of_returning_fake_semantics(
    language: str,
    filename: str,
    old: str,
    new: str,
) -> None:
    differ = SemanticDiffer(DiffConfig(plugin_fuel=1_000, diagnostics=True))

    with pytest.raises(PluginFuelExhausted, match="FUEL_EXCEEDED"):
        differ.diff_strings(old, new, filename=filename, language_hint=language)
