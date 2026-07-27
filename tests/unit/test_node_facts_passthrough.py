"""The host must carry parser-emitted ``NodeFacts`` through unchanged.

The Rust python-parser computes privacy-safe structural facts (param_count,
returns, body, …) and serialises them on each definition ``SemanticNode``. The
Python host only needs to preserve the field so it reaches the VS Code
extension — it must not drop or mangle it.
"""

from __future__ import annotations

from intentdiff.core.models import NodeFacts, SemanticNode


def _node_json(with_facts: bool) -> dict:
    node: dict = {
        "id": "0.1",
        "node_type": "function_definition",
        "label": "ccc",
        "position": {"start_line": 0, "start_col": 0, "end_line": 1, "end_col": 8},
        "structural_hash": "deadbeef",
    }
    if with_facts:
        node["facts"] = {"param_count": 0, "returns": "none", "body": "stub"}
    return node


def test_node_facts_survive_validation_and_dump() -> None:
    node = SemanticNode.model_validate(_node_json(with_facts=True))
    assert node.facts is not None
    assert node.facts.param_count == 0
    assert node.facts.returns == "none"
    assert node.facts.body == "stub"

    dumped = node.model_dump(exclude_none=True)
    assert dumped["facts"] == {"param_count": 0, "returns": "none", "body": "stub"}


def test_node_facts_optional() -> None:
    node = SemanticNode.model_validate(_node_json(with_facts=False))
    assert node.facts is None


def test_node_facts_model_defaults_are_all_optional() -> None:
    facts = NodeFacts()
    assert facts.param_count is None
    assert facts.returns is None
    assert facts.body is None
    assert facts.is_async is None
    assert facts.is_generator is None
    assert facts.decorator_count is None
