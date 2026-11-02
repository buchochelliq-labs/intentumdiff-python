"""Keep the public Python DTO lossless for Rust-authored NodeFacts (#226)."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from intentumdiff.core.models import SemanticDiff


class RustNodeFactsDtoContractTests(unittest.TestCase):
    @staticmethod
    def _node(node_id: str, facts: dict) -> dict:
        return {
            "id": node_id,
            "node_type": "pair",
            "label": "name",
            "position": {
                "start_line": 0, "start_col": 0,
                "end_line": 0, "end_col": 8,
            },
            "structural_hash": "source-hash",
            "children": [],
            "facts": facts,
        }

    @classmethod
    def _diff(cls, facts: dict) -> dict:
        return {
            "old_filename": "settings.json",
            "new_filename": "settings.json",
            "language": "json",
            "changes": [{
                "change_type": "MODIFICATION",
                "old_node": cls._node("old", facts),
                "new_node": cls._node("new", facts),
            }],
            "change_groups": [],
            "parse_errors": [],
            "guardrail_violations": [],
            "has_semantic_changes": True,
            "is_style_only": False,
            "is_fallback": False,
            "metadata": {"engine_owner": "rust"},
        }

    def test_keyed_facts_survive_nested_changed_node_and_json_roundtrip(self):
        facts = {
            "shape": "pair", "child_count": 2, "value_kind": "scalar",
            "item_count": 0, "nesting": 0, "is_leaf": True,
        }
        dto = SemanticDiff.model_validate(self._diff(facts))
        self.assertEqual(dto.changes[0].new_node.facts.model_dump(exclude_none=True), facts)
        serialized = dto.model_dump(mode="json", exclude_none=True)
        for side in ("old_node", "new_node"):
            self.assertEqual(serialized["changes"][0][side]["facts"], facts)
        self.assertEqual(
            SemanticDiff.model_validate(serialized).changes[0].new_node.facts.model_dump(exclude_none=True),
            facts,
        )

    def test_hcl_resource_facts_keep_explicit_zero_and_false(self):
        facts = {
            "attribute_count": 4,
            "nested_block_count": 0,
            "has_nested_block": False,
        }
        dto = SemanticDiff.model_validate(self._diff(facts))
        self.assertEqual(dto.changes[0].old_node.facts.model_dump(exclude_none=True), facts)

    def test_unknown_rust_fact_fails_instead_of_silent_data_loss(self):
        facts = {"shape": "pair", "unexpected_raw_source": "sensitive-value"}
        with self.assertRaises(ValidationError):
            SemanticDiff.model_validate(self._diff(facts))

    def test_existing_function_facts_remain_available(self):
        facts = {"param_count": 2, "returns": "value", "has_computation": False}
        dto = SemanticDiff.model_validate(self._diff(facts))
        self.assertEqual(dto.changes[0].new_node.facts.model_dump(exclude_none=True), facts)


if __name__ == "__main__":
    unittest.main()
