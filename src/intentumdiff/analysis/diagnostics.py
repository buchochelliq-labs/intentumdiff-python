"""Bounded opt-in diagnostics trace helpers for the diff pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from intentumdiff.core.models import Change, ChangeGroup, ChangeType, SemanticNode


DIAGNOSTICS_VERSION = 2
_PAIR_SAMPLE_LIMIT = 8
_CANDIDATE_SAMPLE_LIMIT = 8


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

    def record_candidate_summary(
        self,
        *,
        stage: str,
        rule_id: str,
        action: str = "candidate_summary",
        reason: str = "",
        evaluated_count: int = 0,
        accepted_count: int = 0,
        rejected_count: int = 0,
        rejection_reasons: Mapping[str, int] | None = None,
        accepted_samples: Sequence[Mapping[str, Any]] = (),
        rejected_samples: Sequence[Mapping[str, Any]] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record bounded accepted/rejected candidate evidence."""

        payload = {
            "evaluated_count": evaluated_count,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "rejection_reasons": dict(rejection_reasons or {}),
            "accepted_samples": list(accepted_samples[:_CANDIDATE_SAMPLE_LIMIT]),
            "rejected_samples": list(rejected_samples[:_CANDIDATE_SAMPLE_LIMIT]),
            **dict(metadata or {}),
        }
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

    def record_change(
        self,
        *,
        stage: str,
        change: Change,
        action: str = "change",
        rule_id: str = "",
        reason: str = "",
    ) -> None:
        change_type = (
            change.change_type.value
            if isinstance(change.change_type, ChangeType)
            else str(change.change_type)
        )
        self.record(
            stage=stage,
            action=action,
            rule_id=rule_id,
            reason=reason,
            old_nodes=[change.old_node],
            new_nodes=[change.new_node],
            confidence=change.confidence,
            metadata={
                "change_type": change_type,
                "description": change.description,
                "refactoring_kind": (
                    change.refactoring_kind.value
                    if change.refactoring_kind is not None
                    else None
                ),
            },
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": DIAGNOSTICS_VERSION,
            "summary": _json_safe(self.summary),
            "events": list(self.events),
            "dropped_events": self.dropped_events,
        }


def record_matching_delta(
    recorder: DiagnosticsRecorder,
    *,
    stage: str,
    rule_id: str,
    before: Sequence[Any],
    after: Sequence[Any],
) -> None:
    """Record before/after matching counts and sampled pair changes."""

    if not recorder.enabled:
        return

    before_pairs = {_pair_key(match): match for match in before}
    after_pairs = {_pair_key(match): match for match in after}
    added = [after_pairs[key] for key in after_pairs.keys() - before_pairs.keys()]
    removed = [before_pairs[key] for key in before_pairs.keys() - after_pairs.keys()]
    if not added and not removed and len(before_pairs) == len(after_pairs):
        recorder.record(
            stage=stage,
            action="augment_matching",
            rule_id=rule_id,
            reason="no matching changes",
            metadata={"before_pairs": len(before_pairs), "after_pairs": len(after_pairs)},
        )
        return

    recorder.record(
        stage=stage,
        action="augment_matching",
        rule_id=rule_id,
        reason="matching augmentation applied",
        metadata={
            "before_pairs": len(before_pairs),
            "after_pairs": len(after_pairs),
            "added_pair_count": len(added),
            "removed_pair_count": len(removed),
            "added_pairs": [_pair_digest(match) for match in added[:_PAIR_SAMPLE_LIMIT]],
            "removed_pairs": [
                _pair_digest(match) for match in removed[:_PAIR_SAMPLE_LIMIT]
            ],
        },
    )


def _pair_key(match: Any) -> tuple[str, str]:
    return (match.old_node.id, match.new_node.id)


def _pair_digest(match: Any) -> dict[str, Any]:
    return {
        "old_id": match.old_node.id,
        "new_id": match.new_node.id,
        "old_type": match.old_node.node_type,
        "new_type": match.new_node.node_type,
        "old_label": match.old_node.label,
        "new_label": match.new_node.label,
    }


def node_digest(node: SemanticNode | None) -> dict[str, Any]:
    """Return a small diagnostics-safe node reference."""

    if node is None:
        return {}
    return {
        "id": node.id,
        "type": node.node_type,
        "label": node.label,
    }


def pair_digest(
    old_node: SemanticNode | None,
    new_node: SemanticNode | None,
    *,
    score: float | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Return a small diagnostics-safe old/new candidate reference."""

    payload: dict[str, Any] = {
        "old": node_digest(old_node),
        "new": node_digest(new_node),
    }
    if score is not None:
        payload["score"] = round(score, 4)
    if reason:
        payload["reason"] = reason
    return payload


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
