"""Standard scenarios for bash (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestBash(CodeScenarioSuite):
    language = "bash"
    filename = "s.sh"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'f() {\n  :\n}\n',
            'f() {\n  echo Hello\n}\n',
            exact_total=2),
    }
