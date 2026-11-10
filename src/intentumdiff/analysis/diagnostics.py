"""Bounded opt-in diagnostics trace helpers for the diff pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from intentumdiff.core.models import ChangeGroup, SemanticNode


DIAGNOSTICS_VERSION = 2
@dataclass
class DiagnosticsRecorder:
    """Collect JSON-safe diagnostics events when enabled."""

    enabled: bool = False
    max_events: int = 500
    events: list[dict[str, Any]] = field(default_factory=list)
    dropped_events: int = 0
    summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> "DiagnosticsRecorder":
        return cls(enabled=False)

    def record(
        self,
        *,
        stage: str,
        action: str,
        rule_id: str = "",
        reason: str = "",
        old_nodes: Sequence[SemanticNode | None] = (),
        new_nodes: Sequence[SemanticNode | None] = (),
        old_node_ids: Sequence[str] = (),
        new_node_ids: Sequence[str] = (),
        old_labels: Sequence[str] = (),
        new_labels: Sequence[str] = (),
        confidence: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        if len(self.events) >= self.max_events:
            self.dropped_events += 1
            return

        old_node_list = [node for node in old_nodes if node is not None]
        new_node_list = [node for node in new_nodes if node is not None]
        event_metadata = dict(metadata or {})
        if old_node_list:
            event_metadata.setdefault(
                "old_node_types", [node.node_type for node in old_node_list]
            )
        if new_node_list:
            event_metadata.setdefault(
                "new_node_types", [node.node_type for node in new_node_list]
            )

        event = {
            "stage": stage,
            "rule_id": rule_id,
            "action": action,
            "reason": reason,
            "old_node_ids": list(old_node_ids)
            or [node.id for node in old_node_list],
            "new_node_ids": list(new_node_ids)
            or [node.id for node in new_node_list],
            "old_labels": list(old_labels)
            or [node.label for node in old_node_list if node.label],
            "new_labels": list(new_labels)
            or [node.label for node in new_node_list if node.label],
            "confidence": confidence,
            "metadata": _json_safe(event_metadata),
        }
        self.events.append(event)
        stage_counts = self.summary.setdefault("events_by_stage", {})
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    def record_count(
        self,
        *,
        stage: str,
        action: str,
        count: int,
        rule_id: str = "",
        reason: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        payload = {"count": count, **dict(metadata or {})}
        self.record(
            stage=stage,
            action=action,
            rule_id=rule_id,
            reason=reason,
            metadata=payload,
        )

    def record_group(self, *, stage: str, group: ChangeGroup) -> None:
        kind = group.kind.value if hasattr(group.kind, "value") else str(group.kind)
        self.record(
            stage=stage,
            action=kind.lower(),
            rule_id=group.rule_id,
            reason=str(group.metadata.get("reason", "")),
            old_node_ids=group.old_node_ids,
            new_node_ids=group.new_node_ids,
            old_labels=group.old_labels,
            new_labels=group.new_labels,
            confidence=group.confidence,
            metadata={
                "raw_change_indices": group.raw_change_indices,
                "refactoring_kind": (
                    group.refactoring_kind.value
                    if group.refactoring_kind is not None
                    else None
                ),
                **dict(group.metadata),
            },
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": DIAGNOSTICS_VERSION,
            "summary": _json_safe(self.summary),
            "events": list(self.events),
            "dropped_events": self.dropped_events,
        }


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item, depth=depth + 1) for item in value]
    if hasattr(value, "value"):
        return value.value
    return str(value)
