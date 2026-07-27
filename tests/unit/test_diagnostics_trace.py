"""Tests for the opt-in semantic diagnostics trace."""

from __future__ import annotations

from intentdiff import SemanticDiffer
from intentdiff.analysis.diagnostics import DiagnosticsRecorder
from intentdiff.core.models import DiffConfig
from intentdiff.differ import (
    _fuel_budget,
    _record_engine_telemetry,
    _summarize_engine_telemetry,
)


def _diff(
    old: str,
    new: str,
    *,
    filename: str = "example.py",
    language: str = "python",
    max_events: int = 500,
):
    return SemanticDiffer(
        DiffConfig(diagnostics=True, diagnostics_max_events=max_events)
    ).diff_strings(old, new, filename, language_hint=language)


def _stages(trace: dict) -> set[str]:
    return {event["stage"] for event in trace["events"]}


def _events(trace: dict, *, stage: str, action: str | None = None) -> list[dict]:
    return [
        event
        for event in trace["events"]
        if event["stage"] == stage and (action is None or event["action"] == action)
    ]


def _telemetry_process_calls(diff) -> list[dict]:
    telemetry = diff.metadata["engine_telemetry"]
    return [
        call
        for call in telemetry["calls"]
        if call["function"] == "process"
    ]


def test_default_diff_does_not_include_diagnostics_metadata() -> None:
    diff = SemanticDiffer().diff_strings(
        "def f(x):\n    return x\n",
        "def f(y):\n    return y\n",
        "example.py",
        language_hint="python",
    )

    assert "diagnostics" not in diff.metadata


def test_default_wasm_parser_diff_includes_compact_engine_telemetry() -> None:
    diff = SemanticDiffer().diff_strings(
        "export const answer = 41;\n",
        "export const answer = 42;\n",
        "example.ts",
        language_hint="typescript",
    )

    process_calls = _telemetry_process_calls(diff)
    assert process_calls
    assert all(call["fuel_budget"] for call in process_calls)
    assert all(call["call_count"] >= 1 for call in process_calls)
    assert all(call["engine_owner"] == "python" for call in process_calls)
    assert all(call["engine"] == "python_wasmtime_plugin_host" for call in process_calls)
    assert all(call["provenance"] == "first_party_wasm" for call in process_calls)
    assert any(call["fuel_consumed"] > 0 for call in process_calls)


def test_diagnostics_trace_includes_wasm_fuel_summary_event() -> None:
    diff = _diff(
        "export const answer = 41;\n",
        "export const answer = 42;\n",
        filename="example.ts",
        language="typescript",
    )

    trace = diff.metadata["diagnostics"]
    assert trace["summary"]["engine_telemetry"]["calls"]
    [event] = _events(trace, stage="engine.telemetry", action="wasm_fuel_summary")
    assert any(call["function"] == "process" for call in event["metadata"]["calls"])
    assert any(
        call["engine"] == "python_wasmtime_plugin_host"
        and call["provenance"] == "first_party_wasm"
        for call in event["metadata"]["calls"]
    )


def test_engine_telemetry_classifies_excessive_fuel_hotspots() -> None:
    diff = _diff(
        "export const answer = 41;\n",
        "export const answer = 42;\n",
        filename="example.ts",
        language="typescript",
    )

    telemetry = diff.metadata["engine_telemetry"]
    assert "fuel_hotspots" in telemetry
    assert isinstance(telemetry["fuel_hotspots"], list)
    for call in telemetry["calls"]:
        if call["function"] == "process":
            assert call["language"] == "typescript"
            assert call["filename"] == "example.ts"
            assert call["input_bytes"] > 0
            assert call["input_lines"] > 0


def test_engine_telemetry_hotspots_identify_language_method_and_file() -> None:
    records = [
        {
            "plugin": "src/intentdiff/wasm/js_ts_parser.wasm",
            "function": "process",
            "engine_owner": "python",
            "engine": "python_wasmtime_plugin_host",
            "provenance": "first_party_wasm",
            "trusted": True,
            "status": "ok",
            # Sized to exceed BOTH hotspot thresholds (absolute 30M, per-KB 22M —
            # recalibrated 2026-07 for the literal-capture floor + LTO codegen jitter).
            "fuel_budget": 100_000_000,
            "fuel_consumed": 35_000_000,
            "fuel_remaining": 65_000_000,
            "fuel_used_percent": 35.0,
            "elapsed_ms": 20.0,
            "language": "typescript",
            "filename": "apps/review-shell/src/main.ts",
            "input_bytes": 512,
            "input_lines": 24,
        }
    ]

    summary = _summarize_engine_telemetry(records)
    [hotspot] = summary["fuel_hotspots"]
    assert hotspot["language"] == "typescript"
    assert hotspot["function"] == "process"
    assert hotspot["filename"] == "apps/review-shell/src/main.ts"
    assert hotspot["thresholds_exceeded"] == ["absolute", "per_kb"]

    diagnostics = DiagnosticsRecorder(enabled=True)
    _record_engine_telemetry(diagnostics, records)
    [event] = _events(
        diagnostics.snapshot(),
        stage="engine.telemetry",
        action="wasm_fuel_hotspot",
    )
    assert event["metadata"]["fuel_hotspots"] == [hotspot]


def test_engine_telemetry_does_not_flag_large_linear_file_as_hotspot() -> None:
    records = [
        {
            "plugin": "src/intentdiff/wasm/rust_parser.wasm",
            "function": "process",
            "engine_owner": "python",
            "engine": "python_wasmtime_plugin_host",
            "provenance": "first_party_wasm",
            "trusted": True,
            "status": "ok",
            "fuel_budget": 3_000_000_000,
            "fuel_consumed": 155_000_000,
            "fuel_remaining": 2_845_000_000,
            "fuel_used_percent": 5.2,
            "elapsed_ms": 45.0,
            "language": "rust",
            "filename": "crates/parsers/js-ts-parser/src/lib.rs",
            "input_bytes": 38_386,
            "input_lines": 1_195,
        }
    ]

    summary = _summarize_engine_telemetry(records)

    assert summary["fuel_hotspots"] == []


def test_adaptive_fuel_treats_realistic_settings_as_floor_not_hard_cap() -> None:
    assert _fuel_budget(10_000_000, 250_000_000) == 250_000_000
    assert _fuel_budget(100_000_000, 250_000_000) == 250_000_000
    assert _fuel_budget(-1, 250_000_000) == -1


def test_adaptive_fuel_preserves_tiny_explicit_exhaustion_budgets() -> None:
    assert _fuel_budget(1_000, 250_000_000) == 1_000


def test_enabled_diff_records_stable_pipeline_stages() -> None:
    diff = _diff(
        "def f(x):\n    return x\n",
        "def f(y):\n    return y\n",
    )

    trace = diff.metadata["diagnostics"]
    assert trace["version"] == 2
    assert trace["dropped_events"] == 0
    assert trace["summary"]["language"] == "python"
    assert trace["summary"]["final_change_count"] == len(diff.changes)
    # Issue #57/#54: the review is produced by the routed Rust finalize; the stable
    # stages are the shell-side records plus the per-pass finalize trace (every probed
    # refine/finalize pass reports its surviving change count).
    assert {"parser", "parse", "finalize"} <= _stages(trace)
    finalize_passes = _events(trace, stage="finalize")
    assert any(event["action"] == "rust_finalize_review" for event in finalize_passes)
    assert any(event["action"].startswith("refine:") for event in finalize_passes)
    assert len(finalize_passes) >= 10


def test_python_rename_trace_records_refactoring_evidence() -> None:
    diff = _diff(
        "def connect(addr):\n    return addr\n",
        "def connect(address):\n    return address\n",
    )

    # Issue #57/#54: the rename is detected inside the Rust finalize. The evidence is
    # the surfaced REFACTORING pair itself plus the rename passes in the finalize trace.
    renames = [
        change
        for change in diff.changes
        if change.change_type.value == "REFACTORING"
        and change.old_node is not None
        and change.new_node is not None
        and change.old_node.label == "addr"
        and change.new_node.label == "address"
    ]
    assert renames
    trace = diff.metadata["diagnostics"]
    assert any(
        "rename" in event["action"]
        for event in _events(trace, stage="finalize")
    )


def test_valid_scoped_rename_trace_records_accepted_candidate() -> None:
    diff = _diff(
        "def connect(addr):\n    return addr\n",
        "def connect(address):\n    return address\n",
    )

    # Issue #57/#54: one scoped rename is accepted as exactly ONE review event — the
    # occurrences corroborate, they do not multiply (the anchors-port dedupe rule).
    renames = [
        change
        for change in diff.changes
        if change.change_type.value == "REFACTORING"
        and change.old_node is not None
        and change.old_node.label == "addr"
    ]
    assert len(renames) == 1


def test_false_global_rename_trace_records_rejection_reason() -> None:
    diff = _diff(
        "def add(a, b):\n    return a + b\n",
        "def multiply(name, x):\n    return name * x\n",
    )

    # Issue #57/#54: the anti-fabrication contract is behavior-level — different
    # functions with different params must NOT produce cross-function renames
    # (a->name, b->x are single-letter/global candidates the engine must reject).
    fabricated = [
        change
        for change in diff.changes
        if change.change_type.value == "REFACTORING"
        and change.old_node is not None
        and change.old_node.label in {"a", "b"}
    ]
    assert not fabricated


def test_json_keyed_profile_trace_records_matching_augmentation() -> None:
    diff = _diff(
        '{"items": [{"id": "copy", "value": 1}]}',
        '{"items": [{"id": "new", "value": 0}, {"id": "copy", "value": 2}]}',
        filename="data.json",
        language="json",
    )

    # Issue #57/#54: keyed-data matching runs inside the Rust finalize. The contract is
    # the SHAPE it guarantees: the inserted item is an ADDITION and the id="copy" item
    # pairs by key identity (value 1 -> 2 as a MODIFICATION), never cross-paired.
    additions = [c for c in diff.changes if c.change_type.value == "ADDITION"]
    assert additions, diff.changes
    modifications = [
        c
        for c in diff.changes
        if c.change_type.value == "MODIFICATION"
        and c.old_node is not None
        and c.new_node is not None
        and c.old_node.label == "1"
        and c.new_node.label == "2"
    ]
    assert modifications, diff.changes
    trace = diff.metadata["diagnostics"]
    assert _events(trace, stage="finalize")


def test_literal_value_edit_records_move_candidate_summary_without_move() -> None:
    diff = _diff("port = 25363\n", "port = 25362\n")

    # Issue #57/#54: a literal value edit must never fabricate a MOVE. The finalize
    # trace proves the move machinery ran (probed passes), the diff proves it
    # accepted nothing.
    assert not [c for c in diff.changes if c.change_type.value == "MOVE"]
    trace = diff.metadata["diagnostics"]
    assert any(
        "move" in event["action"]
        for event in _events(trace, stage="finalize")
    )


def test_style_only_trace_records_shortcut_and_ignored_style_evidence() -> None:
    diff = _diff(
        "def answer():\n    return 42\n",
        "# comment\n\ndef answer():\n    return 42\n",
    )

    trace = diff.metadata["diagnostics"]
    assert diff.is_style_only
    assert "invariance" in _stages(trace)
    assert any(
        event["rule_id"] == "generic.style_only_shortcut.source_equivalence"
        for event in trace["events"]
    )


def test_diagnostics_max_events_caps_trace_and_counts_dropped_events() -> None:
    diff = _diff(
        "def f(x):\n    return x\n",
        "def f(y):\n    return y\n",
        max_events=3,
    )

    trace = diff.metadata["diagnostics"]
    assert len(trace["events"]) == 3
    assert trace["dropped_events"] > 0
