"""Standard scenarios for perl (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestPerl(CodeScenarioSuite):
    language = "perl"
    filename = "s.pl"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'sub f {\n}\n',
            'sub f {\n    print "Hello\\n";\n}\n',
            exact_total=1),
    }
