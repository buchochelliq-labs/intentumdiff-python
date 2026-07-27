"""Standard scenarios for ruby (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestRuby(CodeScenarioSuite):
    language = "ruby"
    filename = "m.rb"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'def f\nend\n',
            "def f\n  puts 'Hello'\nend\n",
            exact_total=1),
    }
