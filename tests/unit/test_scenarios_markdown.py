"""Standard scenarios for markdown (tree-sitter parser, issue #44 — structural shapes)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, MarkdownScenarioSuite

_MD = "# Title\n\nAn intro paragraph.\n\n## Section A\n\nBody of A.\n"


class TestMarkdown(MarkdownScenarioSuite):
    language = "markdown"
    filename = "doc.md"
    CASES = {
        # markdown-specific (section-aware)
        # Structural shapes since the tree-sitter parser (issue #44): sections and list
        # items are REAL nodes labeled by their content — no `#`/`-` marker prefixes,
        # and a heading rename is a first-class section RENAME, not a line-text edit.
        "add_section_at_end": Case(_MD, _MD + "\n## Section B\n\nBody of B.\n", additions=("Section B",)),
        "add_bullet": Case("# Todo\n\n- one\n- two\n", "# Todo\n\n- one\n- two\n- three\n", additions=("three",), exact_total=1),
        "rename_heading": Case("# Old Title\n\nBody stays.\n", "# New Title\n\nBody stays.\n", renames=(("Old Title", "New Title"),), exact_total=1),
        # Fixed 2026-07-06 (issue #15): LIS discrimination — a swap is ONE section move; the
        # body travels with the section (relocated lines net out, blank churn suppressed).
        "move_section": Case("# T\n\n## A\n\nbody a\n\n## B\n\nbody b\n", "# T\n\n## B\n\nbody b\n\n## A\n\nbody a\n", exact_total=1),
        # inherited line scenarios (markdown content)
        "modify_line": Case(_MD, _MD.replace("An intro paragraph.", "An updated paragraph."), modifications=(("An intro paragraph.", "An updated paragraph."),), exact_total=1, forbid_node_types=("text_span",)),
        "delete_in_middle": Case(_MD, _MD.replace("Body of A.\n", ""), deletions=("Body of A.",), exact_total=1),
        "identical": Case(_MD, _MD, style_only=True),
    }
