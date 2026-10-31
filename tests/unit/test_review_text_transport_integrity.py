"""The public text review must not normalize truncated Rust success into no change."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from intentumdiff.api import review_text


class ReviewTextTransportIntegrityTests(unittest.TestCase):
    @staticmethod
    def intact_result() -> dict:
        return {
            "old_filename": "document.md",
            "new_filename": "document.md",
            "language": "generic",
            "changes": [],
            "change_groups": [],
            "parse_errors": [],
            "guardrail_violations": [],
            "has_semantic_changes": False,
            "is_style_only": False,
            "is_fallback": False,
            "metadata": {"engine_owner": "rust", "semantic_contract": "generic_text_review_native_v1"},
        }

    def test_intact_rust_result_is_accepted_without_python_semantic_reclassification(self):
        with patch("intentumdiff.rust_core._c_abi_call", return_value=self.intact_result()) as call:
            diff = review_text("same\n", "same\n", filename="document.md")
        self.assertEqual(diff.changes, [])
        self.assertFalse(diff.has_semantic_changes)
        self.assertEqual(diff.metadata["engine_owner"], "rust")
        call.assert_called_once_with("review_text", "same\n", "same\n", "document.md", "document.md")

    def test_missing_authoritative_rust_field_is_never_a_success(self):
        for field in (
            "changes", "change_groups", "parse_errors", "guardrail_violations",
            "has_semantic_changes", "is_style_only", "is_fallback", "metadata",
        ):
            with self.subTest(field=field):
                response = self.intact_result()
                response.pop(field)
                with patch("intentumdiff.rust_core._c_abi_call", return_value=response):
                    with self.assertRaisesRegex(ValueError, "Rust complete batch diff requires"):
                        review_text("# Before\n", "# After\n", filename="document.md")

    def test_malformed_authoritative_rust_field_is_never_a_success(self):
        for field, value in (
            ("changes", None), ("change_groups", {}), ("parse_errors", ""),
            ("guardrail_violations", None), ("has_semantic_changes", "false"),
            ("is_style_only", 0), ("is_fallback", 1), ("metadata", []),
        ):
            with self.subTest(field=field, value=value):
                response = self.intact_result()
                response[field] = value
                with patch("intentumdiff.rust_core._c_abi_call", return_value=response):
                    with self.assertRaisesRegex(ValueError, "Rust complete batch diff requires"):
                        review_text("# Before\n", "# After\n", filename="document.md")

    def test_non_object_rust_success_is_rejected(self):
        for payload in (None, [], 0, ""):
            with self.subTest(payload=payload):
                with patch("intentumdiff.rust_core._c_abi_call", return_value=payload):
                    with self.assertRaisesRegex(ValueError, "Rust complete batch diff must be an object"):
                        review_text("a", "b")


if __name__ == "__main__":
    unittest.main()
