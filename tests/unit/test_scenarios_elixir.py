"""Standard scenarios for elixir (hardening matrix, issue #42)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite


class TestElixir(CodeScenarioSuite):
    language = "elixir"
    filename = "m.ex"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'defmodule M do\n  def f do\n  end\nend\n',
            'defmodule M do\n  def f do\n    IO.puts("Hello")\n  end\nend\n',
            exact_total=1),
    }
