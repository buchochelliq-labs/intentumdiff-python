"""
tests/unit/test_renderer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Snapshot-style tests for ``SemanticDiffer.render()``.

Because the tests run without installed Wasm plugins, the ``"terminal"`` and
``"json"`` built-in formats are tested directly.  Plugin-backed formats
(``"patch"``, ``"html"``, ``"llm"``) are tested with a mocked registry.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from intentdiff.core.models import (
    Change,
    ChangeType,
    DiffConfig,
    NodePosition,
    RefactoringKind,
    SemanticDiff,
    SemanticNode,
)
from intentdiff.differ import SemanticDiffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos() -> NodePosition:
    return NodePosition(start_line=0, start_col=0, end_line=1, end_col=0)


def _node(node_type: str = "identifier", label: str = "foo") -> SemanticNode:
    return SemanticNode(
        id="n1",
        node_type=node_type,
        label=label,
        position=_pos(),
        structural_hash="h1",
    )


def _diff(changes: list[Change] | None = None) -> SemanticDiff:
    return SemanticDiff(
        old_filename="old.py",
        new_filename="new.py",
        language="python",
        has_semantic_changes=bool(changes),
        changes=changes or [],
    )


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------


class TestRenderJson:
    def test_json_is_valid(self):
        diff = _diff()
        differ = SemanticDiffer()
        output = differ.render(diff, output_format="json")
        parsed = json.loads(output)
        assert parsed["old_filename"] == "old.py"
        assert parsed["language"] == "python"

    def test_json_contains_changes(self):
        change = Change(
            change_type=ChangeType.ADDITION,
            new_node=_node(),
            description="Added identifier 'foo'",
        )
        diff = _diff([change])
        differ = SemanticDiffer()
        output = differ.render(diff, output_format="json")
        parsed = json.loads(output)
        assert len(parsed["changes"]) == 1
        assert parsed["changes"][0]["change_type"] == "ADDITION"

    def test_json_is_indented(self):
        diff = _diff()
        output = SemanticDiffer().render(diff, output_format="json")
        # Indented JSON always contains newlines
        assert "\n" in output


# ---------------------------------------------------------------------------
# Terminal format
# ---------------------------------------------------------------------------


class TestRenderTerminal:
    def test_terminal_contains_filenames(self):
        diff = _diff()
        output = SemanticDiffer().render(diff, output_format="terminal")
        assert "old.py" in output
        assert "new.py" in output

    def test_terminal_contains_language(self):
        diff = _diff()
        output = SemanticDiffer().render(diff, output_format="terminal")
        assert "python" in output

    def test_terminal_contains_change_descriptions(self):
        change = Change(
            change_type=ChangeType.DELETION,
            old_node=_node(),
            description="Delete foo",
        )
        diff = _diff([change])
        output = SemanticDiffer().render(diff, output_format="terminal")
        assert "DELETION" in output
        assert "Delete foo" in output

    def test_terminal_no_ansi_codes(self):
        """no_color=True ensures the output contains no ANSI escape sequences."""
        diff = _diff()
        output = SemanticDiffer().render(diff)
        assert "\x1b[" not in output

    def test_terminal_default_format(self):
        """render(diff) with no format argument defaults to 'terminal'."""
        diff = _diff()
        output = SemanticDiffer().render(diff)
        assert "old.py" in output


# ---------------------------------------------------------------------------
# Plugin-backed format (mocked registry)
# ---------------------------------------------------------------------------


class TestRenderPluginFormat:
    def test_unknown_format_raises_value_error(self):
        diff = _diff()
        differ = SemanticDiffer()
        with pytest.raises(ValueError, match="No renderer"):
            differ.render(diff, output_format="nonexistent_format")

    def test_plugin_render_is_called_with_diff_json(self):
        renderer_mock = MagicMock()
        renderer_mock.format_name = "patch"
        renderer_mock.priority = 0
        renderer_mock.render.return_value = "--- patch output ---"

        registry_mock = MagicMock()
        registry_mock.get_renderer.return_value = renderer_mock

        diff = _diff()
        differ = SemanticDiffer(registry=registry_mock)
        output = differ.render(diff, output_format="patch")

        registry_mock.get_renderer.assert_called_once_with("patch")
        renderer_mock.render.assert_called_once()
        assert output == "--- patch output ---"

    def test_plugin_render_receives_valid_json(self):
        """The JSON passed to the renderer plugin must be parseable."""
        received_json: list[str] = []

        def capture_render(diff_json: str, fuel: int | None = None) -> str:
            received_json.append(diff_json)
            return "ok"

        renderer_mock = MagicMock()
        renderer_mock.format_name = "html"
        renderer_mock.priority = 0
        renderer_mock.render.side_effect = capture_render

        registry_mock = MagicMock()
        registry_mock.get_renderer.return_value = renderer_mock

        change = Change(
            change_type=ChangeType.ADDITION,
            new_node=_node(),
            description="Added foo",
        )
        diff = _diff([change])
        SemanticDiffer(registry=registry_mock).render(diff, output_format="html")

        assert len(received_json) == 1
        parsed = json.loads(received_json[0])
        assert parsed["language"] == "python"
        assert len(parsed["changes"]) == 1
