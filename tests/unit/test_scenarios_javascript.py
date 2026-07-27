"""Standard scenarios for javascript (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestJavascript(CodeScenarioSuite):
    language = "javascript"
    filename = "a.js"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'function f() {}\n',
            "function f() {\n  console.log('Hello')\n}\n",
            exact_total=1),
    }
