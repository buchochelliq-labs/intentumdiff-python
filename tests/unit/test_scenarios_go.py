"""Standard scenarios for go (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestGo(CodeScenarioSuite):
    language = "go"
    filename = "a.go"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'package main\n\nfunc f() {}\n',
            'package main\n\nfunc f() {\n\tprintln("Hello")\n}\n',
            exact_total=1),
    }
