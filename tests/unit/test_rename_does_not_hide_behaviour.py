"""A rename must never hide a behavioural change in the same function.

# Why this exists

Renaming a function while editing its body is one of the commonest shapes in real review —
extract, rename, adjust. The engine collapsed that into a single `REFACTORING` change and
discarded the body edits with the DELETION/ADDITION pair it replaced.

`REFACTORING` means "structure changed, behaviour did not". So the tool was not merely missing
a change; it was *asserting the change was safe* in its own vocabulary, and a reviewer acting
on that label would skim past it.

The guard that allowed it asked "does the function start on the same line?" and, if so, treated
the rename as compatible without ever comparing the bodies. Same start line is evidence of
nothing.

See intentumdiff-core#18. The Rust half lives in `draft_suppressors.rs`; this is the
acceptance half proving it survives the binding.
"""

from __future__ import annotations

from intentumdiff import SemanticDiffer

BEFORE = "def total(cart):\n    return sum(i.price for i in cart)\n"

RENAMED_ONLY = "def calculate_total(cart):\n    return sum(i.price for i in cart)\n"

# Totals over 100 now get 10% off. A reviewer MUST see this.
BODY_CHANGED = (
    "def total(cart):\n"
    "    subtotal = sum(i.price for i in cart)\n"
    "    if subtotal > 100:\n"
    "        return subtotal * 0.9\n"
    "    return subtotal\n"
)

RENAMED_AND_BODY_CHANGED = (
    "def calculate_total(cart):\n"
    "    subtotal = sum(i.price for i in cart)\n"
    "    if subtotal > 100:\n"
    "        return subtotal * 0.9\n"
    "    return subtotal\n"
)


def _kinds(old: str, new: str) -> list[str]:
    diff = SemanticDiffer().diff_strings(old, new, "billing.py")
    return [str(c.change_type).rsplit(".", 1)[-1] for c in diff.changes]


def test_a_rename_with_a_changed_body_is_not_reported_as_refactoring() -> None:
    """THE regression.

    Not "reports more changes" — the load-bearing assertion is that it does NOT claim
    REFACTORING, because that label tells a reviewer the behaviour is unchanged.
    """
    kinds = _kinds(BEFORE, RENAMED_AND_BODY_CHANGED)
    assert "REFACTORING" not in kinds, (
        "A rename that also changed the body was labelled REFACTORING — which asserts "
        f"behaviour did not change. The discount logic would be skimmed. Got: {kinds}"
    )
    assert kinds, "a rename plus a behavioural change must report something"


def test_a_pure_rename_is_still_one_refactoring() -> None:
    """The fix must not be 'disable rename detection'.

    A rename with an untouched body is exactly what REFACTORING is for, and collapsing it to
    one change is the value the feature adds.
    """
    assert _kinds(BEFORE, RENAMED_ONLY) == ["REFACTORING"]


def test_a_body_change_without_a_rename_is_unaffected() -> None:
    """Control: the path that always worked must keep working."""
    kinds = _kinds(BEFORE, BODY_CHANGED)
    assert "REFACTORING" not in kinds
    assert len(kinds) >= 3, f"expected the inserted statements to surface, got {kinds}"
