"""File lifecycle, phase profiling, fuel and telemetry helpers for intentumdiff.differ."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from intentumdiff.analysis.diagnostics import (
    DiagnosticsRecorder,
)
from intentumdiff.core.models import (
    ChangeGroupKind,
    SemanticDiff,
)

logger = logging.getLogger(__name__)

_ADDED_FILE_STATUSES = frozenset({"added", "add", "a", "new", "untracked"})
_DELETED_FILE_STATUSES = frozenset({"deleted", "delete", "d", "removed", "remove"})


def _infer_file_lifecycle(
    old_source: str,
    new_source: str,
    staging_status: str | None = None,
) -> str:
    """Infer the file lifecycle from source presence and git status facts.

    This is shell-owned source metadata, not semantic classification. The engine
    still owns the raw additions/deletions and review groups.
    """

    status = (staging_status or "").strip().lower()
    if status in _ADDED_FILE_STATUSES:
        return "added"
    if status in _DELETED_FILE_STATUSES:
        return "deleted"
    if old_source == "" and new_source != "":
        return "added"
    if old_source != "" and new_source == "":
        return "deleted"
    return "modified"


def _apply_file_lifecycle_to_diff(
    diff: SemanticDiff,
    lifecycle: str,
) -> SemanticDiff:
    metadata = dict(diff.metadata)
    metadata["file_lifecycle"] = lifecycle
    if lifecycle == "modified":
        return diff.model_copy(update={"metadata": metadata})

    change_groups = [
        group for group in diff.change_groups if group.kind != ChangeGroupKind.IGNORED_STYLE
    ]
    return diff.model_copy(
        update={
            "metadata": metadata,
            "change_groups": change_groups,
            "is_style_only": False,
            "has_semantic_changes": bool(diff.changes),
        }
    )


class _PhaseProfiler:
    """Tiny opt-in wall-clock phase timer used by benchmark/debug paths."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._started = time.perf_counter()
        self._phases: list[dict[str, Any]] = []

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - started) * 1000)

    def record(self, name: str, duration_ms: float, *, shared: bool = False) -> None:
        if not self.enabled:
            return
        entry: dict[str, Any] = {
            "name": name,
            "duration_ms": round(duration_ms, 3),
        }
        if shared:
            entry["shared"] = True
        self._phases.append(entry)

    def snapshot(self) -> dict[str, Any]:
        total_ms = (time.perf_counter() - self._started) * 1000
        return {
            "schema_version": 1,
            "total_ms": round(total_ms, 3),
            "phases": list(self._phases),
        }


_DEFAULT_PLUGIN_FUEL = 100_000_000
_EXPLICIT_FUEL_EXHAUSTION_TEST_CAP = 1_000_000
# Hotspot thresholds are calibrated to the measured worst legitimate parser plus ~30%
# headroom. Two effects moved the floor in 2026-07: literal-container text capture in the
# tree-sitter converters (issue #46 — a deliberate correctness cost), and whole-binary LTO
# (lto=true, codegen-units=1) letting ANY crate change reshuffle the tree-sitter parse
# loop's codegen by ±10-15% between rebuilds. Measured post-rebuild worst cases:
# powershell 30-func 16.4M/KB, typescript tiny-file 20.5M absolute. Thresholds exist to
# catch pathological (quadratic) parsers, not codegen jitter on the heaviest grammars.
_FUEL_HOTSPOT_ABSOLUTE = 30_000_000
_FUEL_HOTSPOT_PER_KB = 22_000_000.0
_FUEL_HOTSPOT_PER_LINE = 1_500_000.0


def _attach_content_type_metadata(
    diff: SemanticDiff, old_content: str, new_content: str
) -> SemanticDiff:
    """Enrich a diff's metadata with the detected content type (mime + category).

    Uses the Rust magic-byte detector on the changed content so downstream
    surfaces can show what kind of file was diffed. Never raises — the detector
    bridge falls back gracefully when the native core is unavailable.
    """
    from intentumdiff.content_type import detect_content_type

    head = (new_content or old_content)[:8192].encode("utf-8", errors="replace")
    metadata = dict(diff.metadata or {})
    metadata["content_type"] = detect_content_type(head)
    return diff.model_copy(update={"metadata": metadata})


def _fuel_budget(base: int, extra: int) -> int:
    """Return an adaptive budget without hiding explicit low-fuel tests.

    Real editor/CLI fuel settings are a floor, not a guarantee that ordinary
    source files will fail once they cross that exact number. Tiny budgets are
    still respected so fuel-pressure tests can prove runaway plugins fail
    explicitly instead of being silently promoted to a larger cap.
    """
    if base == -1:
        return base
    if base < _EXPLICIT_FUEL_EXHAUSTION_TEST_CAP:
        return base
    return max(base, extra)


def _drain_plugin_telemetry(plugin_adapter: Any) -> list[dict[str, Any]]:
    drain = getattr(plugin_adapter, "drain_telemetry", None)
    if not callable(drain):
        return []
    records = drain()
    return records if isinstance(records, list) else []


def _summarize_engine_telemetry(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_plugin: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        plugin = str(record.get("plugin") or "?")
        function = str(record.get("function") or "?")
        key = (plugin, function)
        entry = by_plugin.setdefault(
            key,
            {
                "plugin": plugin,
                "function": function,
                "engine_owner": record.get("engine_owner") or "unknown",
                "engine": record.get("engine") or "unknown",
                "provenance": record.get("provenance") or "unknown",
                "call_count": 0,
                "elapsed_ms": 0.0,
                "fuel_budget": record.get("fuel_budget"),
                "fuel_consumed": 0,
                "total_fuel_consumed": 0,
                "max_fuel_used_percent": None,
                "language": record.get("language"),
                "filename": record.get("filename"),
                "input_bytes": 0,
                "input_lines": 0,
                "statuses": {},
                "trusted": bool(record.get("trusted")),
            },
        )
        entry["call_count"] += 1
        entry["elapsed_ms"] = round(
            float(entry["elapsed_ms"]) + float(record.get("elapsed_ms") or 0.0),
            3,
        )
        consumed = record.get("fuel_consumed")
        if isinstance(consumed, int):
            entry["fuel_consumed"] = max(entry["fuel_consumed"], consumed)
            entry["total_fuel_consumed"] += consumed
        input_bytes = record.get("input_bytes")
        if isinstance(input_bytes, int):
            entry["input_bytes"] += input_bytes
        input_lines = record.get("input_lines")
        if isinstance(input_lines, int):
            entry["input_lines"] += input_lines
        if entry.get("language") is None and record.get("language") is not None:
            entry["language"] = record.get("language")
        if entry.get("filename") is None and record.get("filename") is not None:
            entry["filename"] = record.get("filename")
        percent = record.get("fuel_used_percent")
        if isinstance(percent, int | float):
            current = entry["max_fuel_used_percent"]
            entry["max_fuel_used_percent"] = percent if current is None else max(current, percent)
        status = str(record.get("status") or "unknown")
        statuses = entry["statuses"]
        statuses[status] = statuses.get(status, 0) + 1
    calls = sorted(
        by_plugin.values(),
        key=lambda item: (
            item.get("fuel_consumed") or 0,
            item.get("elapsed_ms") or 0.0,
        ),
        reverse=True,
    )
    hotspots = [_fuel_hotspot_for_call(call) for call in calls]
    hotspots = [hotspot for hotspot in hotspots if hotspot is not None]
    return {"schema_version": 1, "calls": calls, "fuel_hotspots": hotspots}


def _fuel_hotspot_for_call(call: dict[str, Any]) -> dict[str, Any] | None:
    consumed = call.get("fuel_consumed")
    if not isinstance(consumed, int) or consumed <= 0:
        return None
    input_bytes = call.get("input_bytes")
    input_lines = call.get("input_lines")
    kb = max((input_bytes or 0) / 1024.0, 1.0)
    lines = max(input_lines or 0, 1)
    fuel_per_kb = consumed / kb
    fuel_per_line = consumed / lines
    exceeded: list[str] = []
    if consumed > _FUEL_HOTSPOT_ABSOLUTE:
        exceeded.append("absolute")
    if fuel_per_kb > _FUEL_HOTSPOT_PER_KB:
        exceeded.append("per_kb")
    if fuel_per_line > _FUEL_HOTSPOT_PER_LINE:
        exceeded.append("per_line")
    normalized_exceeded = any(threshold in exceeded for threshold in ("per_kb", "per_line"))
    if not exceeded or ("absolute" in exceeded and not normalized_exceeded):
        return None
    return {
        "plugin": call.get("plugin"),
        "function": call.get("function"),
        "language": call.get("language"),
        "filename": call.get("filename"),
        "fuel_consumed": consumed,
        "fuel_budget": call.get("fuel_budget"),
        "fuel_used_percent": call.get("max_fuel_used_percent"),
        "input_bytes": input_bytes,
        "input_lines": input_lines,
        "fuel_per_kb": round(fuel_per_kb, 3),
        "fuel_per_line": round(fuel_per_line, 3),
        "thresholds_exceeded": exceeded,
    }


def _attach_run_telemetry(
    diff: SemanticDiff,
    engine_telemetry: list[dict[str, Any]],
    diagnostics: DiagnosticsRecorder,
) -> SemanticDiff:
    """Final metadata attach (engine_telemetry + diagnostics snapshot) shared by the
    normal pipeline tail and the Rust finalize-routed return (issue #57) — the routed
    short-circuit skipped it, dropping the wasm parser telemetry contract."""
    if engine_telemetry:
        telemetry_metadata = dict(diff.metadata)
        telemetry_metadata["engine_telemetry"] = _summarize_engine_telemetry(
            engine_telemetry
        )
        diff = diff.model_copy(update={"metadata": telemetry_metadata})
    if diagnostics.enabled:
        diagnostics_metadata = dict(diff.metadata)
        diagnostics.summary.update(
            {
                "final_change_count": len(diff.changes),
                "final_group_count": len(diff.change_groups),
                "has_semantic_changes": diff.has_semantic_changes,
                "is_style_only": diff.is_style_only,
            }
        )
        diagnostics_metadata["diagnostics"] = diagnostics.snapshot()
        diff = diff.model_copy(update={"metadata": diagnostics_metadata})
    return diff


def _record_engine_telemetry(
    diagnostics: DiagnosticsRecorder,
    records: list[dict[str, Any]],
) -> None:
    if not records:
        return
    summary = _summarize_engine_telemetry(records)
    diagnostics.summary["engine_telemetry"] = summary
    diagnostics.record(
        stage="engine.telemetry",
        action="wasm_fuel_summary",
        rule_id="engine.wasm_fuel_telemetry",
        reason="Wasm plugin calls recorded fuel and duration telemetry",
        metadata=summary,
    )
    hotspots = summary.get("fuel_hotspots")
    if hotspots:
        diagnostics.record(
            stage="engine.telemetry",
            action="wasm_fuel_hotspot",
            rule_id="engine.wasm_fuel_hotspot",
            reason="Wasm plugin fuel use exceeded the excessive-fuel policy",
            metadata={"fuel_hotspots": hotspots},
        )


