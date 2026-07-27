"""Standard scenarios for dart (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestDart(CodeScenarioSuite):
    language = "dart"
    filename = "m.dart"
    CASES = {
        # Fixed 2026-07-06 (issue #51): zero-changes-after-suppression no longer conflates
        # to style-only — the flag is honest. The REMAINING half (the dart scaffold
        # suppressor still eats the body ADDITION, so changes come out empty) is tracked by
        # the edit-matrix dart xfails + issue #42; tighten to exact_total=1 when it lands.
        "trivial_body_to_real_body": Case(
            'void f() {}\n',
            "void f() {\n  print('Hello');\n}\n",
            not_style_only=True),
    }
