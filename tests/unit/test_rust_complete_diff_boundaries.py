"""Never lose Rust-owned semantic results at the remaining diff-to-diff DTO boundaries."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from intentumdiff._differ_runtime import _apply_file_lifecycle_to_diff
from intentumdiff.analysis.guardrails import GuardrailPolicy, apply_guardrails_to_diff
from intentumdiff.core.models import DiffConfig, SemanticDiff
from intentumdiff.differ import SemanticDiffer


class CompleteRustDiffBoundaryTests(unittest.TestCase):
    @staticmethod
    def source_diff() -> SemanticDiff:
        return SemanticDiff(
            old_filename="document.md", new_filename="document.md", language="generic",
            changes=[], change_groups=[], parse_errors=[], guardrail_violations=[],
            has_semantic_changes=False, is_style_only=False, is_fallback=False,
            metadata={"engine_owner": "rust"},
        )

    def test_file_lifecycle_requires_complete_native_result(self):
        original = self.source_diff()
        with patch("intentumdiff.rust_core._c_abi_call", return_value=original.model_dump(mode="json")):
            self.assertEqual(_apply_file_lifecycle_to_diff(original, "modified").metadata["engine_owner"], "rust")
        for bad in ({"old_filename": "document.md", "new_filename": "document.md", "language": "generic"},
                    {**original.model_dump(mode="json"), "changes": None}):
            with self.subTest(bad=bad), patch("intentumdiff.rust_core._c_abi_call", return_value=bad):
                with self.assertRaisesRegex(ValueError, "Rust complete batch diff requires"):
                    _apply_file_lifecycle_to_diff(original, "modified")

    def test_guardrail_policy_requires_complete_native_result(self):
        original = self.source_diff()
        policy = GuardrailPolicy(path=None, rules=())
        args = dict(old_tree=None, new_tree=None, old_source="", new_source="",
                    config=DiffConfig(guardrails_enabled=True))
        with patch("intentumdiff.analysis.guardrails.load_guardrail_policy", return_value=policy):
            with patch("intentumdiff.rust_core._c_abi_call", return_value=original.model_dump(mode="json")):
                self.assertEqual(apply_guardrails_to_diff(original, **args).metadata["engine_owner"], "rust")
            for bad in ({"old_filename": "document.md", "new_filename": "document.md", "language": "generic"},
                        {**original.model_dump(mode="json"), "guardrail_violations": None}):
                with self.subTest(bad=bad), patch("intentumdiff.rust_core._c_abi_call", return_value=bad):
                    with self.assertRaisesRegex(ValueError, "Rust complete batch diff requires"):
                        apply_guardrails_to_diff(original, **args)

    def test_diff_to_diff_analyzer_requires_complete_result(self):
        original = self.source_diff()
        def analyze(output: dict) -> SemanticDiff:
            analyzer = SimpleNamespace(
                provenance="synthetic-parser",
                analyze_diff=lambda *_args, **_kwargs: json.dumps(output),
            )
            registry = SimpleNamespace(get_diff_analyzers=lambda _language: [analyzer])
            shell = SimpleNamespace(_registry=registry)
            return SemanticDiffer._run_diff_analyzers_on_routed(shell, original,
                                                                 "generic", "document.md", 1000)
        self.assertEqual(analyze(original.model_dump(mode="json")).metadata["engine_owner"], "rust")
        for bad in ({"old_filename": "document.md", "new_filename": "document.md", "language": "generic"},
                    {**original.model_dump(mode="json"), "has_semantic_changes": "false"}):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(RuntimeError, "Diff analyzer .*failed"):
                    analyze(bad)


if __name__ == "__main__":
    unittest.main()
