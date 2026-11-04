"""Review transport validation, runnable without optional engine dependencies."""
import importlib.util
from pathlib import Path
import sys
import unittest

_path = Path(__file__).resolve().parents[2] / "src/intentumdiff/github_app.py"
_spec = importlib.util.spec_from_file_location("github_app_under_test", _path)
app = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = app
_spec.loader.exec_module(app)


class ReviewEvidenceTests(unittest.TestCase):
    def payload(self, review=None):
        result = {"repository": {"full_name": "owner/repo"},
                  "pull_request": {"number": 7, "head": {"sha": "abc123"}}}
        if review is not None:
            result["intentumdiff_review"] = review
        return result

    def test_missing_or_incomplete_review_cannot_complete_check(self):
        for review in (None, {}, [], {"passed": True}):
            with self.subTest(review=review), self.assertRaises(app.GitHubAppError):
                app.build_pull_request_check_response(self.payload(review))

    def test_malformed_review_fields_cannot_be_coerced_into_success(self):
        valid = dict(checked_files=2, semantic_changes=1, guardrail_violations=1, passed=False)
        for field, values in {"passed": ["false", 0, 1, None],
                              "checked_files": [-1, True, "2", 1.5, None],
                              "semantic_changes": [-1, False, "0"],
                              "guardrail_violations": [-1, False, "0"]}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(app.GitHubAppError):
                    app.build_pull_request_check_response(self.payload({**valid, field: value}))

    def test_explicit_results_are_preserved_without_python_semantic_decisions(self):
        for passed, expected in ((False, "failure"), (True, "success")):
            review = dict(checked_files=2, semantic_changes=1, guardrail_violations=1, passed=passed)
            result = app.build_pull_request_check_response(self.payload(review))
            self.assertEqual(result["review"], review)
            self.assertEqual(result["check_run"]["conclusion"], expected)

    def test_check_payload_requires_actual_boolean(self):
        with self.assertRaises(app.GitHubAppError):
            app.build_check_run_payload(name="IntentumDiff", head_sha="abc123",
                                        summary_markdown="review", passed="false")


if __name__ == "__main__":
    unittest.main()
