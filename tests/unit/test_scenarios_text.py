"""Standard scenarios for plain text (generic parser, line-oriented)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, LineScenarioSuite

_TXT = "line one\nline two\nline three\nline four\n"


class TestText(LineScenarioSuite):
    language = "text"
    filename = "notes.txt"
    CASES = {
        "add_at_end": Case(_TXT, _TXT + "line five\n", additions=("line five",), exact_total=1),
        "add_in_middle": Case(_TXT, "line one\nline two\ninserted line\nline three\nline four\n", additions=("inserted line",), exact_total=1),
        "add_at_start": Case(_TXT, "line zero\n" + _TXT, additions=("line zero",), exact_total=1),
        "delete_at_end": Case(_TXT, "line one\nline two\nline three\n", deletions=("line four",), exact_total=1),
        "delete_in_middle": Case(_TXT, "line one\nline three\nline four\n", deletions=("line two",), exact_total=1),
        "delete_at_start": Case(_TXT, "line two\nline three\nline four\n", deletions=("line one",), exact_total=1),
        "modify_line": Case(_TXT, "line one\nline TWO edited\nline three\nline four\n", modifications=(("line two", "line TWO edited"),), exact_total=1, forbid_node_types=("text_span",)),
        "modify_two_lines": Case(_TXT, "line ONE\nline two\nline THREE\nline four\n", modifications=(("line one", "line ONE"), ("line three", "line THREE")), exact_total=2, forbid_node_types=("text_span",)),
        # Fixed 2026-07-06 (issue #14): identical lines relocated within a prose file net out.
        "reorder_lines": Case(_TXT, "line two\nline one\nline three\nline four\n", exact_total=0),
        "whitespace_only": Case(_TXT, "line one\nline two\n   \nline three\nline four\n", exact_total=0),
        "identical": Case(_TXT, _TXT, style_only=True),
    }
