"""Tests for opt-in phase timing metadata."""

from __future__ import annotations

from typing import Any

from intentumdiff import DiffConfig, SemanticDiffer
from intentumdiff.core.models import SemanticDiff


def test_phase_profile_metadata_is_disabled_by_default(monkeypatch: Any) -> None:
    def fake_stages(
        self: SemanticDiffer,
        old_content: str,
        new_content: str,
        filename: str,
        language_hint: str | None,
        **kwargs: Any,
    ) -> SemanticDiff:
        return SemanticDiff(old_filename=filename, new_filename=filename, language="python")

    monkeypatch.setattr(SemanticDiffer, "_run_stages_1_to_11", fake_stages)

    diff = SemanticDiffer(DiffConfig()).diff_strings("old", "new", "example.py")

    assert "phase_timings" not in diff.metadata


def test_phase_profile_metadata_records_named_phases(monkeypatch: Any) -> None:
    def fake_stages(
        self: SemanticDiffer,
        old_content: str,
        new_content: str,
        filename: str,
        language_hint: str | None,
        **kwargs: Any,
    ) -> SemanticDiff:
        profiler = kwargs["profiler"]
        with profiler.phase("parser_selection"):
            pass
        return SemanticDiff(old_filename=filename, new_filename=filename, language="python")

    monkeypatch.setattr(SemanticDiffer, "_run_stages_1_to_11", fake_stages)

    diff = SemanticDiffer(DiffConfig(profile_phases=True)).diff_strings(
        "old",
        "new",
        "example.py",
    )

    profile = diff.metadata["phase_timings"]
    assert profile["schema_version"] == 1
    assert profile["total_ms"] >= 0
    assert profile["phases"][0]["name"] == "parser_selection"
    assert profile["phases"][0]["duration_ms"] >= 0
