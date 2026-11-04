"""The cross-language structural facts (#9) survive the Rust -> Python boundary.

These are derived from the PRUNED canonical tree rather than from any one grammar, so they
are available for every language rather than only the ones with a bespoke extractor. That
makes them the first facts a non-Python binding can rely on, which is why they are worth
pinning from this side as well as from Rust.

WHY THESE EXIST: the Rust core computed all three and asserted them in 12 of its own tests,
and every one of those passed - while `NodeFacts` carried no fields for them, so pydantic
dropped them silently at the DTO boundary. A user of the Python API saw nothing. Rust being
the certified path does not help if the value never reaches the caller, and no test on either
side was watching the join.
"""

from __future__ import annotations

from intentumdiff.differ import SemanticDiffer
from intentumdiff.sources.string_source import StringSource


def _function_facts(old: str, new: str, filename: str = "m.py"):
    """Facts on the function node of a rename, which puts the function itself in the diff."""
    diff = SemanticDiffer().diff(StringSource(old, new, filename=filename))
    for change in diff.changes:
        node = change.new_node or change.old_node
        if node is not None and node.facts is not None and "function" in node.node_type:
            return node.facts.model_dump()
    raise AssertionError(f"no function node with facts in: {[c.change_type for c in diff.changes]}")


def test_a_guard_clause_reaches_the_python_api():
    """The full shape: negated condition + early return = a guard clause."""
    facts = _function_facts(
        "def send(msg):\n    if not msg:\n        return None\n    return deliver(msg)\n",
        "def transmit(msg):\n    if not msg:\n        return None\n    return deliver(msg)\n",
    )
    assert facts["has_guard_clause"] is True
    assert facts["early_exit_count"] == 1
    assert facts["negated_condition_count"] == 1


def test_early_exits_are_counted_without_a_guard():
    """Early exits are counted on their own merits.

    Two `return None`s behind non-negated conditions: the count is 2, and `has_guard_clause`
    stays absent because there is no negated condition to make it a guard. Absent is the
    honest answer here, not False - see the tri-state note below.
    """
    facts = _function_facts(
        "def check(x):\n    if x < 0:\n        return None\n"
        "    if x > 100:\n        return None\n    return x\n",
        "def validate(x):\n    if x < 0:\n        return None\n"
        "    if x > 100:\n        return None\n    return x\n",
    )
    assert facts["early_exit_count"] == 2
    assert facts["negated_condition_count"] is None


def test_absent_means_not_determinable_not_false():
    """`has_guard_clause` is tri-state, and the distinction is load-bearing.

    The pruned tree can prove a guard is PRESENT but cannot prove one is ABSENT - statement
    order survives pruning, operators do not. So `None` means "not determinable here".
    Collapsing it to False would let a consumer conclude "this function has no guard clause"
    from evidence that does not support it, which is worse than saying nothing.
    """
    facts = _function_facts(
        "def add(a, b):\n    return a + b\n",
        "def total(a, b):\n    return a + b\n",
    )
    assert facts["has_guard_clause"] is None
    assert facts["early_exit_count"] is None


def test_the_fields_exist_on_the_model_at_all():
    """The regression guard proper.

    The defect was not a wrong value - it was a MISSING FIELD. pydantic drops unknown keys
    silently, so the facts arrived from Rust and evaporated. Asserting the fields exist on the
    model catches a future removal or rename even if no fixture happens to populate them.
    """
    from intentumdiff.core.models import NodeFacts

    fields = set(NodeFacts.model_fields)
    for name in ("early_exit_count", "negated_condition_count", "has_guard_clause"):
        assert name in fields, f"{name} missing from NodeFacts - Rust emits it, so it would be dropped"
