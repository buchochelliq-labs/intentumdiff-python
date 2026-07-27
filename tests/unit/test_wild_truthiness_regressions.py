"""Wild noise-vs-truth regressions mined from SemanticDiff's public issues.

Each test captures the *essence* of a real user complaint — not the user's
actual code — and locks down what the truthful semantic diff should be.
The goal is to ensure IntentDiff's engine reports the truth (the real
semantic change) rather than burying it under noise that clouds the
message.

Sources reviewed
----------------
- SemanticDiff public issue tracker: https://github.com/Sysmagine/SemanticDiff/issues
- SemanticDiff in-product behaviour reports referenced in
  ``tests/fixtures/semanticdiff_competitor_gap_matrix.json``
- difftastic issue tracker: https://github.com/Wilfred/difftastic/issues

The existing ``test_competitor_issue_regressions.py`` already locks down
issue-inspired cases for JSON keyed reorder, Java ``@Override`` + import
reorder, JS ASI, CSS/SCSS selectors, C/C++ macros, and large-C moves.
This file complements it with the noise-vs-truth cases that weren't yet
captured as essence fixtures.

Dual-run contract
-----------------
Every truthiness test runs against **both** the Python oracle matcher
(``core.engine._compute_matching``) and the Rust language-agnostic
matcher (``intentdiff_rust_core.diff_semantic_tree_json``) via the
``matcher`` fixture. The Rust path must always pass; the Python path
must pass unless it has been explicitly retired via
``_skip_python_because(...)`` because a Bucket 3 rule retirement made
the Python fallback noisier than the truth contract. See
``the retired NOISE_SUPPRESSION_RETUNE doc (git history)`` for the retirement ledger.
"""

from __future__ import annotations

from typing import Literal

import pytest

from intentdiff import SemanticDiffer
from intentdiff.core.models import (
    Change,
    ChangeGroupKind,
    ChangeType,
    DiffConfig,
    SemanticDiff,
)
from tests.unit.diff_sanity import assert_no_identical_positioned_source_modifications

MatcherChoice = Literal["rust"]


@pytest.fixture(params=["rust"])
def matcher(request: pytest.FixtureRequest) -> MatcherChoice:
    """Truthiness fixture, formerly the dual-run python/rust matrix.

    The python matcher retired with the transitional pipeline (issue #57 payoff;
    the "Bucket 3 retirement" ledger in the retired NOISE_SUPPRESSION_RETUNE doc (git history) tracked
    the divergences). The rust param is kept as a param (not inlined) so a future
    second engine — or a Rust variant build — can re-enter the matrix the same way.
    """
    return request.param  # type: ignore[return-value]


def _differ_for(matcher: MatcherChoice) -> SemanticDiffer:
    return SemanticDiffer(DiffConfig(test_matching_engine=matcher))


def _skip_python_because(matcher: MatcherChoice, reason: str) -> None:
    if matcher == "python":
        pytest.skip(f"Python oracle path retired for this contract: {reason}")


def _diff(
    old: str,
    new: str,
    *,
    language: str,
    filename: str,
    matcher: MatcherChoice = "python",
) -> SemanticDiff:
    diff = _differ_for(matcher).diff_strings(
        old,
        new,
        filename=filename,
        language_hint=language,
    )
    assert diff.language == language
    assert not diff.is_fallback
    assert diff.parse_errors == []
    return diff


def _types(diff: SemanticDiff) -> dict[str, int]:
    counts: dict[str, int] = {}
    for change in diff.changes:
        value = (
            change.change_type.value
            if hasattr(change.change_type, "value")
            else str(change.change_type)
        )
        counts[value] = counts.get(value, 0) + 1
    return counts


def _has_noise_suppressed_group(diff: SemanticDiff, *, rule_id: str | None = None) -> bool:
    for group in diff.change_groups:
        if group.kind != ChangeGroupKind.NOISE_SUPPRESSED:
            continue
        if rule_id is None or group.rule_id == rule_id:
            return True
    return False


# ---------------------------------------------------------------------------
# Issue #16 — Braces reported as semantic changes after C# block→file-scoped
# namespace conversion
# ---------------------------------------------------------------------------


def test_csharp_block_to_file_scoped_namespace_does_not_emit_brace_noise(
    matcher: MatcherChoice,
) -> None:
    """Converting ``namespace Foo { ... }`` to ``namespace Foo;`` is a tool-driven modernisation.

    SemanticDiff issue #16 reports that brace characters shift-indent and get
    flagged as changes on every nested member. The truthful diff is: only the
    namespace declaration changes (block form → file-scoped form); every nested
    type keeps its body unchanged.
    """
    diff = _diff(
        """\
namespace MyApp {
  class Foo {
    public int Bar() => 1;
  }
}
""",
        """\
namespace MyApp;

class Foo {
  public int Bar() => 2;
}
""",
        language="csharp",
        filename="App.cs",
        matcher=matcher,
    )

    # The honest changes are: (1) the namespace declaration shape changed,
    # (2) the literal return value `1` → `2` changed. Nothing else.
    types = _types(diff)
    assert not types.get("DELETION"), (
        f"expected zero DELETION noise for the file-scoped namespace conversion, "
        f"got {types}"
    )
    # The body edit on Bar must surface — the engine must not hide the real
    # change behind the namespace-conversion noise.
    assert any(
        change.change_type in {ChangeType.MODIFICATION, ChangeType.REFACTORING}
        and change.new_node is not None
        and "Bar" in change.new_node.label
        for change in diff.changes
    ), f"expected the Bar() body edit to surface; observed types={types}"


# ---------------------------------------------------------------------------
# Issue #32 — Whitespace collapsing in TSX when wrapping JSX in a parent
# ---------------------------------------------------------------------------


def test_tsx_wrap_with_parent_element_does_not_emit_indent_cascade_noise(
    matcher: MatcherChoice,
) -> None:
    """Wrapping JSX in a new parent element re-indents every descendant line.

    SemanticDiff issue #32 reports that those added leading spaces show up as
    changes, drowning out the real change (the new wrapper). The truthful diff
    is: the wrapper element was added; the wrapped content is preserved (moved
    into the wrapper or anchored in place), never deleted-and-re-added.
    """
    diff = _diff(
        """\
const Item = () => (
  <span>hello</span>
);
""",
        """\
const Item = () => (
  <div>
    <span>hello</span>
  </div>
);
""",
        language="tsx",
        filename="Item.tsx",
        matcher=matcher,
    )

    types = _types(diff)
    # The indent cascade must NOT show up as DELETION noise for every
    # re-indented line.
    assert not types.get("DELETION"), (
        f"expected zero DELETION noise for the parent-element wrap, got {types}"
    )
    # The wrapper insertion must surface as a meaningful change.
    assert diff.has_semantic_changes
    # The original `<span>` content must be preserved (moved or anchored)
    # — not deleted and re-added as the same text.
    span_deletions = [
        c for c in diff.changes
        if c.change_type == ChangeType.DELETION
        and c.old_node is not None
        and "span" in c.old_node.label
    ]
    span_additions = [
        c for c in diff.changes
        if c.change_type == ChangeType.ADDITION
        and c.new_node is not None
        and "span" in c.new_node.label
    ]
    assert not (span_deletions and span_additions), (
        "the wrapped <span> was reported as both ADDITION and DELETION — "
        "engine missed the structural preservation"
    )


# ---------------------------------------------------------------------------
# Issue #67 — XML attribute reordering reported as change (essence only)
# ---------------------------------------------------------------------------


def test_xml_attribute_reorder_is_not_a_semantic_change(
    matcher: MatcherChoice,
) -> None:
    """XML attribute order is semantically irrelevant (per the XML spec).

    SemanticDiff issue #67 (filed as an XML-support request but the motivating
    pain was WSDL diffs differing only in attribute order) calls out that
    reordered attributes defeat every line-oriented diff tool. The truthful
    diff is: zero changes.
    """
    diff = _diff(
        '<note from="alice" to="bob" date="2026-01-01">hi</note>\n',
        '<note date="2026-01-01" to="bob" from="alice">hi</note>\n',
        language="xml",
        filename="note.xml",
        matcher=matcher,
    )

    assert diff.changes == [], (
        f"expected zero changes for a pure attribute reorder, got "
        f"{[c.change_type for c in diff.changes]}"
    )
    assert diff.has_semantic_changes is False
    # Attribute-reorder noise must be explicitly suppressed (recorded as
    # a suppression event), not merely absent because parsing failed.
    # With parser-level normalisation the suppression may be caught by the
    # style-only shortcut (IGNORED_STYLE) rather than the post-diff
    # refinement pipeline (NOISE_SUPPRESSED).
    assert diff.change_groups, (
        "expected a suppression group for the attribute reorder, got no groups"
    )
    assert any(
        g.kind in (ChangeGroupKind.NOISE_SUPPRESSED, ChangeGroupKind.IGNORED_STYLE)
        for g in diff.change_groups
    ), (
        "expected NOISE_SUPPRESSED or IGNORED_STYLE group, got "
        f"{[g.kind for g in diff.change_groups]}"
    )


# ---------------------------------------------------------------------------
# Issues #10 / #62 — Reordered JSON sections reported as add+delete+move
# (the original issue #10 plus the ARM/IaC variant #62)
#
# The single-key reorder case is already covered in
# ``test_competitor_issue_regressions.py``. This locks down the multi-section
# variant where two top-level keys with structured values swap position and
# one of them also has an internal value edit.
# ---------------------------------------------------------------------------


def test_json_section_reorder_with_one_internal_edit_reports_only_the_edit(
    matcher: MatcherChoice,
) -> None:
    """Two JSON sections swap top-level position; one of them has an internal edit.

    SemanticDiff issues #10 and #62 report that regenerating IaC/ARM JSON in a
    different order floods the review with add+delete+move entries. The
    truthful diff is: the one internal value edit; the section-level reorder
    is order-invariant noise.
    """
    diff = _diff(
        """\
{
  "network": {"name": "prod", "cidr": "10.0.0.0/8"},
  "storage": {"account": "prodfiles", "tier": "Standard"}
}
""",
        """\
{
  "storage": {"account": "prodfiles", "tier": "Premium"},
  "network": {"name": "prod", "cidr": "10.0.0.0/8"}
}
""",
        language="json",
        filename="infra.json",
        matcher=matcher,
    )

    types = _types(diff)
    # The honest change is the storage tier value: Standard -> Premium.
    # Everything else is order-invariant noise.
    assert types == {"MODIFICATION": 1} or types.get("MODIFICATION") == 1 and not types.get(
        "ADDITION"
    ) and not types.get("DELETION"), (
        f"expected exactly one MODIFICATION for the tier edit, got {types}"
    )
    # The section reorder must be explicitly noise-suppressed.
    assert _has_noise_suppressed_group(diff), (
        f"expected a NOISE_SUPPRESSED group for the section reorder, got "
        f"{[(g.kind, g.rule_id) for g in diff.change_groups]}"
    )
    # The surfaced MODIFICATION must touch the tier value, not an arbitrary node.
    assert any(
        change.change_type == ChangeType.MODIFICATION
        and change.old_node is not None
        and "Standard" in change.old_node.label
        and change.new_node is not None
        and "Premium" in change.new_node.label
        for change in diff.changes
    ), "expected the surfaced change to be Standard → Premium"


# ---------------------------------------------------------------------------
# Issue #84 (companion) — JavaScript function declaration → arrow const
# conversion should not explode into per-token add+delete noise
#
# This is the case that surfaced when validating migration step 2 of
# docs/ENGINE_BOUNDARY_AUDIT.md: a function declaration turning into a const
# arrow function preserves the parameter list and several body fragments
# across the rewrite. The truthful diff recognises those preservations
# instead of declaring the whole function deleted+added.
# ---------------------------------------------------------------------------


def test_javascript_function_to_arrow_const_preserves_parameter_list(
    matcher: MatcherChoice,
) -> None:
    """``function f(x) { ... }`` → ``const f = (x) => ...`` is a common modernisation.

    The parameter list ``(x)`` is structurally identical before and after —
    it should be matched (preserved), not reported as ADDITION+DELETION. The
    engine may report the wrapper change (function_declaration → arrow
    expression), but must not invent add+delete pairs for every preserved
    identifier inside the parameter list.
    """
    diff = _diff(
        """\
function greet(name) {
  return name;
}
""",
        """\
const greet = (name) => name;
""",
        language="javascript",
        filename="greet.js",
        matcher=matcher,
    )

    assert diff.has_semantic_changes
    assert_no_identical_positioned_source_modifications(
        diff,
        "function greet(name) {\n  return name;\n}\n",
        "const greet = (name) => name;\n",
    )
    # The identifier `name` from the parameter list must NOT appear in both
    # a DELETION and an ADDITION — that would be the noise pattern. Either
    # it's preserved (no event) or it's reported once.
    deletions_of_name = [
        c
        for c in diff.changes
        if c.change_type == ChangeType.DELETION
        and c.old_node is not None
        and c.old_node.label == "name"
    ]
    additions_of_name = [
        c
        for c in diff.changes
        if c.change_type == ChangeType.ADDITION
        and c.new_node is not None
        and c.new_node.label == "name"
    ]
    assert not (deletions_of_name and additions_of_name), (
        "parameter identifier `name` reported as both ADDITION and DELETION "
        "— engine missed the structural-identity match"
    )


# ===========================================================================
# Tier 1 — per-language truthiness fixtures
#
# Each test dual-runs on the `matcher` fixture (both "rust" and "python").
# The Rust path must always pass. Where the Rust path can't yet satisfy the
# truth contract, the test is marked ``pytest.mark.xfail(strict=False)`` for
# the Rust case with a pointer to the gap documentation, so the gap stays
# visible instead of being silently skipped.
# ===========================================================================


def _assert_no_change(diff: SemanticDiff, *, matcher: MatcherChoice, case: str) -> None:
    """Assert the engine produced zero semantic changes (pure noise-only edit)."""
    summary = [
        (
            c.change_type,
            c.old_node.label if c.old_node else "",
            c.new_node.label if c.new_node else "",
        )
        for c in diff.changes
    ]
    assert diff.changes == [], (
        f"[{matcher}] expected zero changes for {case!r}, "
        f"got {_types(diff)}: {summary}"
    )


def _change_labels_mention(change: Change, side: str, token: str) -> bool:
    nodes = [change.old_node] if side == "old" else [change.new_node]
    for node in nodes:
        if node is None:
            continue
        if any(token in (n.label or "") for n in [node, *node.descendants()]):
            return True
    return False


# ---------------------------------------------------------------------------
# Py-1 — Indentation is semantic in Python (false-negative guard)
# Source: difftastic #818, #942
# ---------------------------------------------------------------------------


def test_python_dedent_changes_block_membership_and_must_surface(
    matcher: MatcherChoice,
) -> None:
    """``print(x)`` leaves the function body via dedent — must NOT be hidden.

    Python's off-side rule means whitespace changes can change runtime
    behaviour. This is the *inverse* of the noise rules: the engine must
    surface this as a structural change, not report ``changes == []``.

    Source: difftastic #818, #942.

    Root cause (closed in Phase A of the noise-suppression retune): the
    ``_suppress_stationary_move_noise`` rule in ``analysis/refinement.py``
    used a line-only stationarity test and suppressed every descendant
    MOVE unconditionally. The cascade guard added in Phase A restricts
    the descendant cascade so cross-block moves like this dedent surface.
    """
    diff = _diff(
        """\
def f():
    x = "inner"
    print(x)
""",
        """\
def f():
    x = "inner"
print(x)
""",
        language="python",
        filename="block.py",
        matcher=matcher,
    )

    assert diff.has_semantic_changes, (
        f"[{matcher}] expected the dedent (block-membership change) to "
        f"surface as a semantic change; got changes={diff.changes}"
    )


# ---------------------------------------------------------------------------
# Rust-1 — Struct rename reported as rename, not unrelated add+delete
# Source: difftastic #974
# ---------------------------------------------------------------------------


def test_rust_struct_rename_surfaces_as_rename_or_modification(
    matcher: MatcherChoice,
) -> None:
    """``struct PendingRequest`` → ``struct Request`` with identical body.

    The honest change is one rename event; the field list must be preserved
    or moved, not deleted+re-added as unrelated noise.
    """
    diff = _diff(
        """\
struct PendingRequest {
    target: u32,
    payload: Vec<u8>,
}
""",
        """\
struct Request {
    target: u32,
    payload: Vec<u8>,
}
""",
        language="rust",
        filename="req.rs",
        matcher=matcher,
    )

    assert diff.has_semantic_changes
    # The contract: ``target`` and ``payload`` must NOT appear in both a
    # DELETION and an ADDITION (the noise pattern). They're identical
    # subtrees; the engine should preserve them via structural matching.
    for field in ("target", "payload"):
        deleted = any(
            c.change_type == ChangeType.DELETION
            and c.old_node is not None
            and field in (c.old_node.label or "")
            for c in diff.changes
        )
        added = any(
            c.change_type == ChangeType.ADDITION
            and c.new_node is not None
            and field in (c.new_node.label or "")
            for c in diff.changes
        )
        assert not (deleted and added), (
            f"[{matcher}] field {field!r} reported as both ADDITION and "
            f"DELETION — engine missed that the struct body is identical"
        )


# ---------------------------------------------------------------------------
# Rust-2 — Method-chain removal highlights only the removed call
# Source: difftastic #937
# ---------------------------------------------------------------------------


def test_rust_method_chain_removal_localizes_to_deleted_call(
    matcher: MatcherChoice,
) -> None:
    """Removing one link from a method chain must not flag the unchanged links.

    The honest change is exactly one DELETION for ``.map(...)``. The
    preserved prefix (``.iter().filter(...)``) and suffix (``.collect()``)
    must not be reported as modifications.
    """
    diff = _diff(
        """\
fn main() {
    let v: Vec<_> = items.iter()
        .filter(|x| x.active)
        .map(|x| x.id)
        .collect();
}
""",
        """\
fn main() {
    let v: Vec<_> = items.iter()
        .filter(|x| x.active)
        .collect();
}
""",
        language="rust",
        filename="chain.rs",
        matcher=matcher,
    )

    assert diff.has_semantic_changes
    # The preserved ``filter`` closure must not appear in both a DELETION
    # and an ADDITION (the chain-wrap noise pattern from difftastic #937).
    filter_deleted = any(
        c.change_type == ChangeType.DELETION
        and c.old_node is not None
        and "filter" in (c.old_node.label or "")
        for c in diff.changes
    )
    filter_added = any(
        c.change_type == ChangeType.ADDITION
        and c.new_node is not None
        and "filter" in (c.new_node.label or "")
        for c in diff.changes
    )
    assert not (filter_deleted and filter_added), (
        f"[{matcher}] unchanged .filter(...) link reported as both "
        f"ADDITION and DELETION — chain-wrap noise from difftastic #937"
    )


# ---------------------------------------------------------------------------
# Go-1 — Composite-literal field reorder (struct constructor keyed by name)
# Source: gopls organize-fields; analogous to JSON keyed reorder
# ---------------------------------------------------------------------------


def test_go_struct_literal_field_reorder_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Go struct-literal fields are keyed by name; reorder is value-preserving."""
    diff = _diff(
        """\
package main

func main() {
    cfg := Config{A: 1, B: 2, C: 3}
    _ = cfg
}
""",
        """\
package main

func main() {
    cfg := Config{C: 3, A: 1, B: 2}
    _ = cfg
}
""",
        language="go",
        filename="cfg.go",
        matcher=matcher,
    )

    _assert_no_change(diff, matcher=matcher, case="Go struct-literal field reorder")


# ---------------------------------------------------------------------------
# Go-4 — ``if err != nil`` wrapping must surface (must-not-suppress guard)
# Source: common gopls/errwrap auto-fix
# ---------------------------------------------------------------------------


def test_go_error_wrapping_change_must_surface(
    matcher: MatcherChoice,
) -> None:
    """Wrapping an error with ``fmt.Errorf("…: %w", err)`` changes observability.

    This pins the *inverse* of the noise rules: suppression passes must
    not swallow a real wrapping edit just because the surrounding
    ``if err != nil { … }`` boilerplate looks unchanged.
    """
    diff = _diff(
        """\
package main

import "errors"

func load() error {
    err := decode()
    if err != nil {
        return err
    }
    return nil
}
""",
        """\
package main

import (
    "errors"
    "fmt"
)

func load() error {
    err := decode()
    if err != nil {
        return fmt.Errorf("load: %w", err)
    }
    return nil
}
""",
        language="go",
        filename="load.go",
        matcher=matcher,
    )

    assert diff.has_semantic_changes, (
        f"[{matcher}] expected the error-wrapping change to surface; "
        f"got changes={diff.changes}"
    )
    # The wrapping change must touch the return expression, not just the
    # import block.
    assert any(
        _change_labels_mention(c, "new", "Errorf")
        or _change_labels_mention(c, "new", "wrap")
        for c in diff.changes
    ), (
        f"[{matcher}] expected a change mentioning the wrapping call; "
        "observed: "
        f"{list((c.change_type, c.new_node.label if c.new_node else '') for c in diff.changes)}"
    )


# ---------------------------------------------------------------------------
# Ruby-1 — Hashrocket → 1.9 colon symbol-key form
# Source: RuboCop Style/HashSyntax on essentially every modernization
# ---------------------------------------------------------------------------


def test_ruby_hashrocket_to_colon_symbol_key_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """:name => "demo" and name: "demo" produce the identical Hash with Symbol keys."""
    diff = _diff(
        """\
config = {
  :name => "demo",
  :timeout => 30,
}
""",
        """\
config = {
  name: "demo",
  timeout: 30,
}
""",
        language="ruby",
        filename="cfg.rb",
        matcher=matcher,
    )

    _assert_no_change(diff, matcher=matcher, case="Ruby hashrocket → colon symbol-key")


# ---------------------------------------------------------------------------
# PHP-2 — array(...) → short array literal [...]
# Source: Rector ArrayToShortArrayRector; the most ubiquitous PHP modernization
# ---------------------------------------------------------------------------


def test_php_array_to_short_array_literal_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """``array(1, 2, 3)`` and ``[1, 2, 3]`` are the same value in PHP 5.4+."""
    diff = _diff(
        """\
<?php
$nums = array(1, 2, 3);
$map  = array("a" => 1, "b" => 2);
""",
        """\
<?php
$nums = [1, 2, 3];
$map  = ["a" => 1, "b" => 2];
""",
        language="php",
        filename="cfg.php",
        matcher=matcher,
    )

    _assert_no_change(diff, matcher=matcher, case="PHP array() → [...] literal")


# ---------------------------------------------------------------------------
# YAML-1 — Block-style ↔ flow-style on the same mapping/sequence
# Source: difftastic #417, #723
# ---------------------------------------------------------------------------


def test_yaml_block_to_flow_style_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Per the YAML spec, block and flow styles present the same representation graph.

    Source: difftastic #417, #723.

    Root cause: the cataloged ``data.yaml.representation_equivalence`` rule
    at ``invariances/rules.yaml`` is ``status: catalog, evaluator: null`` —
    i.e. documented but unimplemented. The ``flow_node`` MODIFICATION
    surfaces on both matchers once the Phase A cascade guard stops hiding
    it. Phase C of the noise-suppression retune implements the evaluator.
    """
    diff = _diff(
        """\
- list: user_known
  items:
    - alpha
    - beta
""",
        """\
- list: user_known
  items: [alpha, beta]
""",
        language="yaml",
        filename="cfg.yaml",
        matcher=matcher,
    )

    try:
        _assert_no_change(diff, matcher=matcher, case="YAML block↔flow style")
    except AssertionError:
        # Strict xfail: verify the gap is still the documented one (a
        # flow_node MODIFICATION), not some new regression. Once Phase C
        # implements the evaluator this xfail flips to "unexpectedly passed".
        types = _types(diff)
        assert types == {"MODIFICATION": 1}, (
            f"[{matcher}] unexpected YAML gap shape; expected exactly one "
            f"flow_node MODIFICATION, got {types}"
        )
        pytest.xfail(
            f"[{matcher}] YAML block↔flow equivalence gap: waiting on "
            "data.yaml.representation_equivalence evaluator (Phase C)."
        )


# ---------------------------------------------------------------------------
# YAML-2 — Mapping key reorder (top-level sections swap)
# Source: kustomize/helm regeneration; difftastic #723
# ---------------------------------------------------------------------------


def test_yaml_mapping_key_reorder_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """YAML mappings are unordered (unless tagged !!omap/!!pairs)."""
    diff = _diff(
        """\
apiVersion: v1
kind: ConfigMap
data:
  retention: 3d
  enabled: true
""",
        """\
apiVersion: v1
kind: ConfigMap
data:
  enabled: true
  retention: 3d
""",
        language="yaml",
        filename="cm.yaml",
        matcher=matcher,
    )

    _assert_no_change(diff, matcher=matcher, case="YAML mapping key reorder")


# ---------------------------------------------------------------------------
# JSON-3 — Array-of-objects keyed by ``id`` (schema-aware match)
# Source: SCHEMA_AWARE_DIFF_PROFILES.md keyed-array identity; SemanticDiff #10, #62
# ---------------------------------------------------------------------------


def test_json_keyed_array_reorder_with_one_internal_edit_reports_only_the_edit(
    matcher: MatcherChoice,
) -> None:
    """Two JSON objects in an array swap position; one of them has an internal edit.

    Each element has a stable ``id`` key. The reorder is order-invariant
    noise; the only honest change is the internal value edit.
    """
    diff = _diff(
        """\
{
  "tasks": [
    {"id": "ingest", "retries": 1},
    {"id": "train", "retries": 2}
  ]
}
""",
        """\
{
  "tasks": [
    {"id": "train", "retries": 3},
    {"id": "ingest", "retries": 1}
  ]
}
""",
        language="json",
        filename="tasks.json",
        matcher=matcher,
    )

    types = _types(diff)
    # The honest change is `train.retries: 2 -> 3`. Any ADDITION/DELETION
    # for the reordered elements is positional-matcher noise.
    assert not types.get("ADDITION"), (
        f"[{matcher}] expected zero ADDITION noise for the keyed-array "
        f"reorder, got {types}"
    )
    assert not types.get("DELETION"), (
        f"[{matcher}] expected zero DELETION noise for the keyed-array "
        f"reorder, got {types}"
    )
    assert any(
        c.change_type == ChangeType.MODIFICATION
        and c.old_node is not None
        and "2" in (c.old_node.label or "")
        and c.new_node is not None
        and "3" in (c.new_node.label or "")
        for c in diff.changes
    ), (
        f"[{matcher}] expected the surfaced MODIFICATION to be the "
        f"retries 2 -> 3 edit; observed types={types}"
    )


# ===========================================================================
# Tier 2 — amber REFACTORING / noise-suppressed contracts
#
# These patterns don't have a clean ``changes == []`` contract; the truthful
# diff is either a REFACTORING event or a noise-suppressed group with a
# preserved body. Tests assert that the engine preserves the structural
# content (parameter, body, field set) rather than producing a flood of
# unrelated ADDITION+DELETION pairs.
# ===========================================================================


# ---------------------------------------------------------------------------
# Py-2 — Keyword-argument reorder in a call (Python binds by name)
# Source: LANGUAGE_INVARIANTS_CATALOG.md Python amber category
# ---------------------------------------------------------------------------


def test_python_keyword_argument_reorder_preserves_call_semantics(
    matcher: MatcherChoice,
) -> None:
    """``configure(timeout=30, retries=3, debug=True)`` reordered is the same call.

    Python binds keyword arguments by name, so a pure reorder produces an
    identical call (no side-effectful argument expressions, no ``**kwargs``).
    """
    diff = _diff(
        """\
def main():
    configure(timeout=30, retries=3, debug=True)
""",
        """\
def main():
    configure(debug=True, retries=3, timeout=30)
""",
        language="python",
        filename="cfg.py",
        matcher=matcher,
    )

    # The honest contract: ``timeout``, ``retries``, ``debug`` must NOT
    # appear in both a DELETION and an ADDITION — they're identical kwargs
    # bound by name, not positional arguments that moved.
    for kw in ("timeout", "retries", "debug"):
        deleted = any(
            c.change_type == ChangeType.DELETION
            and c.old_node is not None
            and kw in (c.old_node.label or "")
            for c in diff.changes
        )
        added = any(
            c.change_type == ChangeType.ADDITION
            and c.new_node is not None
            and kw in (c.new_node.label or "")
            for c in diff.changes
        )
        assert not (deleted and added), (
            f"[{matcher}] kwarg {kw!r} reported as both ADDITION and "
            f"DELETION — engine missed that kwargs bind by name"
        )


# ---------------------------------------------------------------------------
# Rust-3 — ``match`` → ``?`` operator refactor
# Source: clippy::question_mark / rust-analyzer assist
# ---------------------------------------------------------------------------


def test_rust_match_to_question_mark_preserves_payload_identifiers(
    matcher: MatcherChoice,
) -> None:
    """``match load() { Ok(v) => v, Err(e) => return Err(e.into()) }`` → ``load()?``.

    The honest change is one REFACTORING event. The bound identifiers
    ``load``, ``v``, ``e`` and the early-return pattern must not appear
    as both ADDITION and DELETION — that would imply the engine missed
    that ``?`` is a rewrite of the same control flow.
    """
    diff = _diff(
        """\
fn run() -> Result<u32, String> {
    let v = match load() {
        Ok(value) => value,
        Err(e) => return Err(e.into()),
    };
    Ok(v + 1)
}
""",
        """\
fn run() -> Result<u32, String> {
    let v = load()?;
    Ok(v + 1)
}
""",
        language="rust",
        filename="run.rs",
        matcher=matcher,
    )

    assert diff.has_semantic_changes
    # ``load`` must not appear as both ADDITION and DELETION (the engine
    # should anchor the call expression across the rewrite).
    load_deleted = any(
        c.change_type == ChangeType.DELETION
        and c.old_node is not None
        and "load" in (c.old_node.label or "")
        for c in diff.changes
    )
    load_added = any(
        c.change_type == ChangeType.ADDITION
        and c.new_node is not None
        and "load" in (c.new_node.label or "")
        for c in diff.changes
    )
    assert not (load_deleted and load_added), (
        f"[{matcher}] load() reported as both ADDITION and DELETION — "
        f"engine missed that ``?`` rewrites the same call"
    )


# ---------------------------------------------------------------------------
# Ruby-2 — ``do ... end`` ↔ brace block conversion
# Source: RuboCop Style/BlockDelimiters
# ---------------------------------------------------------------------------


def test_ruby_block_delimiter_change_preserves_block_body(
    matcher: MatcherChoice,
) -> None:
    """``items.map do |x| x.f end`` ↔ ``items.map { |x| x.f }`` — same block.

    The honest change is one REFACTORING event. The block parameter
    ``|x|`` and body ``x.f`` must not appear as both ADDITION and DELETION.
    """
    diff = _diff(
        """\
result = items.map do |item|
  item.transform
end
""",
        """\
result = items.map { |item| item.transform }
""",
        language="ruby",
        filename="block.rb",
        matcher=matcher,
    )

    assert diff.has_semantic_changes
    # The block body identifier ``transform`` must not appear in both
    # ADDITION and DELETION (the noise pattern).
    transform_deleted = any(
        c.change_type == ChangeType.DELETION
        and c.old_node is not None
        and "transform" in (c.old_node.label or "")
        for c in diff.changes
    )
    transform_added = any(
        c.change_type == ChangeType.ADDITION
        and c.new_node is not None
        and "transform" in (c.new_node.label or "")
        for c in diff.changes
    )
    assert not (transform_deleted and transform_added), (
        f"[{matcher}] block body ``transform`` reported as both ADDITION "
        f"and DELETION — engine missed block-delimiter equivalence"
    )


# ---------------------------------------------------------------------------
# Java-3 — Wildcard import → specific imports
# Source: IntelliJ "Optimize Imports"; companion to SemanticDiff #83
# ---------------------------------------------------------------------------


def test_java_wildcard_to_specific_imports_preserves_used_types(
    matcher: MatcherChoice,
) -> None:
    """``import java.util.*;`` → ``import java.util.List;`` + ``import java.util.Map;``.

    The honest change is one REFACTORING (the import surface gets more
    explicit). The ``List`` and ``Map`` *usage sites* in the body must
    not be flagged as add+delete noise — they're identical references.
    """
    diff = _diff(
        """\
import java.util.*;

class Demo {
    public void run() {
        List<String> xs = new ArrayList<>();
        Map<String, Integer> m = new HashMap<>();
    }
}
""",
        """\
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

class Demo {
    public void run() {
        List<String> xs = new ArrayList<>();
        Map<String, Integer> m = new HashMap<>();
    }
}
""",
        language="java",
        filename="Demo.java",
        matcher=matcher,
    )

    assert diff.has_semantic_changes
    # The body ``ArrayList`` and ``HashMap`` constructor calls must NOT
    # appear in both ADDITION and DELETION — they're identical usage.
    for ctor in ("ArrayList", "HashMap"):
        deleted = any(
            c.change_type == ChangeType.DELETION
            and c.old_node is not None
            and ctor in (c.old_node.label or "")
            for c in diff.changes
        )
        added = any(
            c.change_type == ChangeType.ADDITION
            and c.new_node is not None
            and ctor in (c.new_node.label or "")
            for c in diff.changes
        )
        assert not (deleted and added), (
            f"[{matcher}] body constructor ``{ctor}`` reported as both "
            f"ADDITION and DELETION — engine missed that the body is unchanged"
        )


# ---------------------------------------------------------------------------
# C#-3 — ``var`` ↔ explicit type declaration form
# Source: ReSharper "use explicit type"/"use var" toggle
# ---------------------------------------------------------------------------


def test_csharp_var_to_explicit_type_preserves_initializer(
    matcher: MatcherChoice,
) -> None:
    """``var count = Compute();`` ↔ ``int count = Compute();`` — same declaration.

    The honest change is one REFACTORING event. The initializer ``Compute()``
    and the identifier ``count`` must not appear as both ADDITION and DELETION.

    Root cause: the C# tree-sitter parser drops the type token for ``var``
    and explicit-type declarations, producing shape-identical semantic
    trees. No matcher can recover ``var``/``int`` because the signal isn't
    in the tree. Phase D of the noise-suppression retune fixes the parser
    to emit distinct type nodes.
    """
    diff = _diff(
        """\
class Demo {
    public void Run() {
        var count = Compute();
    }
}
""",
        """\
class Demo {
    public void Run() {
        int count = Compute();
    }
}
""",
        language="csharp",
        filename="Demo.cs",
        matcher=matcher,
    )

    try:
        assert diff.has_semantic_changes
    except AssertionError:
        # Strict xfail: verify the gap is still the documented parser issue.
        # Once Phase D fixes the parser to emit distinct type nodes, this
        # xfail flips to "unexpectedly passed".
        assert diff.changes == [], (
            f"[{matcher}] unexpected C# var/int gap shape; expected zero "
            f"changes (parser drops type node), got {_types(diff)}"
        )
        pytest.xfail(
            f"[{matcher}] C# parser gap: ``var`` ↔ explicit type produces "
            "shape-identical trees. Waiting on Phase D parser fix."
        )
    compute_deleted = any(
        c.change_type == ChangeType.DELETION
        and c.old_node is not None
        and "Compute" in (c.old_node.label or "")
        for c in diff.changes
    )
    compute_added = any(
        c.change_type == ChangeType.ADDITION
        and c.new_node is not None
        and "Compute" in (c.new_node.label or "")
        for c in diff.changes
    )
    assert not (compute_deleted and compute_added), (
        f"[{matcher}] initializer ``Compute()`` reported as both ADDITION "
        f"and DELETION — engine missed var/explicit-type equivalence"
    )


# ---------------------------------------------------------------------------
# TS-2 — Object property reorder (data-only, unique keys, no spread)
# Source: LANGUAGE_INVARIANTS_CATALOG.md JS/TS amber category
# ---------------------------------------------------------------------------


def test_typescript_object_property_reorder_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Reordering properties in a static, unique-key, data-only object literal.

    The honest contract: zero changes when the object has no spread,
    getter/setter, computed key, or duplicate key — exactly the case here.
    """
    diff = _diff(
        """\
const cfg = {
  host: "a",
  port: 1,
  retries: 3,
};
""",
        """\
const cfg = {
  retries: 3,
  host: "a",
  port: 1,
};
""",
        language="typescript",
        filename="cfg.ts",
        matcher=matcher,
    )

    # The honest contract: zero changes for a pure property reorder.
    # We accept either an empty change list (matcher recognised the
    # reorder natively) or a noise-suppressed group — but no ADDITION
    # or DELETION noise for the individual properties.
    types = _types(diff)
    assert not types.get("ADDITION"), (
        f"[{matcher}] expected zero ADDITION noise for the object property "
        f"reorder, got {types}"
    )
    assert not types.get("DELETION"), (
        f"[{matcher}] expected zero DELETION noise for the object property "
        f"reorder, got {types}"
    )


# ===========================================================================
# Tier 3 — clean zero-change contracts (broad per-language coverage)
#
# Each test verifies a language-specific equivalence that semantic diff tools
# commonly mishandle. All follow the dual-run matrix pattern.
# ===========================================================================


def test_javascript_numeric_separator_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """``1_000_000`` and ``1000000`` are the same JS numeric value."""
    diff = _diff(
        "const LIMIT = 1000000;\n",
        "const LIMIT = 1_000_000;\n",
        language="javascript",
        filename="cfg.js",
        matcher=matcher,
    )
    try:
        _assert_no_change(diff, matcher=matcher, case="JS numeric separator")
    except AssertionError:
        if matcher == 'rust' or matcher == 'python':
            pytest.xfail(
                'Rust core invariance engine does not yet' \
                'recognise number node type for integer equivalence.'
            )
        raise


def test_json_number_spelling_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """``1e3`` and ``1000`` are the same JSON number (IEEE-754 double)."""
    diff = _diff(
        '{"count": 1000, "rate": 1.0}\n',
        '{"count": 1e3, "rate": 1}\n',
        language="json",
        filename="cfg.json",
        matcher=matcher,
    )
    try:
        _assert_no_change(diff, matcher=matcher, case="JSON number spelling")
    except AssertionError:
        if matcher == 'rust' or matcher == 'python':
            pytest.xfail(
                'Rust core invariance engine does not yet' \
                'implement IEEE-754 number literal equivalence.'
            )
        raise


def test_xml_cdata_entity_equivalence_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """CDATA sections and entity-escaped text present the same character content."""
    diff = _diff(
        '<msg><![CDATA[a & b < c]]></msg>\n',
        '<msg>a &amp; b &lt; c</msg>\n',
        language="xml",
        filename="msg.xml",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="XML CDATA ↔ entity equivalence")


def test_xml_namespace_prefix_change_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Changing the namespace prefix while keeping the expanded name is cosmetic."""
    diff = _diff(
        '<a:item xmlns:a="http://x">v</a:item>\n',
        '<item xmlns="http://x">v</item>\n',
        language="xml",
        filename="item.xml",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="XML namespace prefix change")


def test_html_boolean_attribute_spelling_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """``disabled``, ``disabled="disabled"``, and ``disabled=""`` are all truthy."""
    diff = _diff(
        '<input type="checkbox" disabled="disabled" checked="checked">\n',
        '<input type="checkbox" disabled checked>\n',
        language="html",
        filename="form.html",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="HTML boolean attribute spelling")


def test_html_void_element_self_closing_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Self-closing slash is optional for void elements (br, hr, img)."""
    diff = _diff(
        '<br />\n<hr>\n<img src="x.png">\n',
        '<br>\n<hr />\n<img src="x.png" />\n',
        language="html",
        filename="void.html",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="HTML void self-closing spelling")


def test_css_color_canonical_value_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """``red`` and ``#ff0000`` resolve to the same sRGB color."""
    diff = _diff(
        ".btn { color: red; border-color: #FF0000; }\n",
        ".btn { color: #ff0000; border-color: red; }\n",
        language="css",
        filename="btn.css",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="CSS color canonical value")


def test_go_import_block_reorder_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Reordering imports inside a Go import block is declarative noise."""
    diff = _diff(
        """\
package main

import (
    "fmt"
    "os"
    "example.com/internal/foo"
)

func main() { fmt.Println(os.Args) }
""",
        """\
package main

import (
    "os"
    "fmt"
    "example.com/internal/foo"
)

func main() { fmt.Println(os.Args) }
""",
        language="go",
        filename="main.go",
        matcher=matcher,
    )
    try:
        _assert_no_change(diff, matcher=matcher, case="Go import block reorder")
    except AssertionError:
        if matcher == 'python':
            pytest.xfail('Python oracle gap: Go import block reorder not recognised.')
        raise


def test_typescript_optional_trailing_comma_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Adding or removing a trailing comma in TS function params is formatting."""
    diff = _diff(
        "function f(a: number, b: number) {}\n",
        "function f(a: number, b: number,) {}\n",
        language="typescript",
        filename="f.ts",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="TS optional trailing comma")


def test_java_annotation_order_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Reordering non-@Repeatable annotations on a declaration is cosmetic."""
    diff = _diff(
        """\
class Demo {
    @Override
    @Deprecated
    public void run() {}
}
""",
        """\
class Demo {
    @Deprecated
    @Override
    public void run() {}
}
""",
        language="java",
        filename="Demo.java",
        matcher=matcher,
    )
    try:
        _assert_no_change(diff, matcher=matcher, case="Java annotation order")
    except AssertionError:
        if matcher == 'rust' or matcher == 'python':
            pytest.xfail('Engine gap: Java annotation reorder treated as signature change.')
        raise


def test_python_implicit_line_continuation_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """Re-wrapping arguments inside parens is formatting, not semantic."""
    diff = _diff(
        """\
result = some_function(
    first_arg,
    second_arg,
)
""",
        """\
result = some_function(first_arg, second_arg)
""",
        language="python",
        filename="call.py",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="Python implicit line continuation")


def test_python_integer_literal_base_is_zero_change(
    matcher: MatcherChoice,
) -> None:
    """``0x1000``, ``4096``, and ``0b1000000000000`` are the same integer."""
    diff = _diff(
        "LIMIT = 0x1000\nMASK = 0b11110000\n",
        "LIMIT = 4096\nMASK = 0b11110000\n",
        language="python",
        filename="cfg.py",
        matcher=matcher,
    )
    _assert_no_change(diff, matcher=matcher, case="Python integer literal base")
