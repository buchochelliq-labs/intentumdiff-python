"""Project-level protected semantic change guardrails."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from intentdiff.analysis.diagnostics import DiagnosticsRecorder
from intentdiff.analysis.keyed_profiles import KEYED_DATA_LANGUAGES
from intentdiff.analysis.resource_profiles import RESOURCE_PROFILE_LANGUAGES
from intentdiff.core.models import (
    DiffConfig,
    GuardrailSeverity,
    GuardrailViolation,
    SemanticDiff,
    SemanticNode,
)

GUARDRAIL_POLICY_FILENAME = "intentdiff.yaml"
GUARDRAIL_CONFIG_LANGUAGES = KEYED_DATA_LANGUAGES | RESOURCE_PROFILE_LANGUAGES


@dataclass(frozen=True)
class ProtectedRule:
    rule_id: str
    severity: GuardrailSeverity
    language: str
    path: str
    message: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardrailPolicy:
    path: Path | None
    rules: tuple[ProtectedRule, ...]


def guardrails_may_apply(
    filename: str,
    new_filename: str | None,
    config: DiffConfig,
) -> bool:
    """Return true when guardrails may affect the final diff result."""

    if not config.guardrails_enabled:
        return False
    if _is_policy_file(filename) or (new_filename is not None and _is_policy_file(new_filename)):
        return True
    policy_path = _find_policy_path(filename, config.guardrail_policy_path)
    if policy_path is None:
        return False
    try:
        return bool(load_guardrail_policy(filename, explicit_path=policy_path).rules)
    except ValueError:
        return True


def load_guardrail_policy(
    filename: str,
    *,
    explicit_path: Path | None = None,
) -> GuardrailPolicy:
    policy_path = _find_policy_path(filename, explicit_path)
    if policy_path is None:
        return GuardrailPolicy(path=None, rules=())

    raw = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{policy_path} must contain a YAML mapping")

    guardrails = raw.get("guardrails", raw)
    if not isinstance(guardrails, dict):
        raise ValueError(f"{policy_path} guardrails section must be a mapping")

    protected = guardrails.get("protected", [])
    if protected is None:
        protected = []
    if not isinstance(protected, list):
        raise ValueError(f"{policy_path} guardrails.protected must be a list")

    rules = tuple(_parse_rule(item, index, policy_path) for index, item in enumerate(protected))
    return GuardrailPolicy(path=policy_path, rules=rules)


def apply_guardrails_to_diff(
    diff: SemanticDiff,
    *,
    old_tree: SemanticNode | None,
    new_tree: SemanticNode | None,
    old_source: str,
    new_source: str,
    config: DiffConfig,
    diagnostics: DiagnosticsRecorder | None = None,
) -> SemanticDiff:
    if not config.guardrails_enabled:
        return diff

    violations: list[GuardrailViolation] = []
    if (old_source != new_source) and (
        _is_policy_file(diff.old_filename) or _is_policy_file(diff.new_filename)
    ):
        violations.append(
            GuardrailViolation(
                rule_id="intentdiff.policy_file",
                severity=GuardrailSeverity.IMMUTABLE,
                file=diff.new_filename or diff.old_filename,
                language=diff.language,
                semantic_path=GUARDRAIL_POLICY_FILENAME,
                old_value="project policy",
                new_value="project policy",
                message="Project guardrail policy changed",
            )
        )

    policy = load_guardrail_policy(
        diff.new_filename or diff.old_filename,
        explicit_path=config.guardrail_policy_path,
    )
    if policy.rules and old_tree is not None and new_tree is not None:
        violations.extend(_evaluate_policy_rules(diff, old_tree, new_tree, policy.rules))

    if not violations:
        return diff

    if diagnostics is not None and diagnostics.enabled:
        for violation in violations:
            diagnostics.record(
                stage="guardrails",
                action="policy_violation",
                rule_id=violation.rule_id,
                reason=violation.message or "protected semantic path changed",
                old_node_ids=[violation.old_node_id] if violation.old_node_id else [],
                new_node_ids=[violation.new_node_id] if violation.new_node_id else [],
                old_labels=[violation.old_value] if violation.old_value else [],
                new_labels=[violation.new_value] if violation.new_value else [],
                confidence=1.0,
                metadata={
                    "severity": violation.severity.value,
                    "file": violation.file,
                    "language": violation.language,
                    "semantic_path": violation.semantic_path,
                    **(
                        {
                            "line": violation.position.start_line,
                            "column": violation.position.start_col,
                        }
                        if violation.position is not None
                        else {}
                    ),
                },
            )

    metadata = dict(diff.metadata)
    metadata["guardrails"] = {
        "violation_count": len(violations),
        "immutable_count": sum(
            violation.severity == GuardrailSeverity.IMMUTABLE for violation in violations
        ),
    }
    return diff.model_copy(
        update={
            "guardrail_violations": [*diff.guardrail_violations, *violations],
            "metadata": metadata,
        }
    )


def _parse_rule(raw: Any, index: int, policy_path: Path) -> ProtectedRule:
    if not isinstance(raw, dict):
        raise ValueError(f"{policy_path} guardrails.protected[{index}] must be a mapping")

    language = str(raw.get("language", "")).strip().lower()
    if language not in GUARDRAIL_CONFIG_LANGUAGES:
        raise ValueError(
            f"{policy_path} guardrails.protected[{index}] targets unsupported "
            f"language {language!r}; protected guardrails currently support "
            f"{sorted(GUARDRAIL_CONFIG_LANGUAGES)}"
        )

    path = _normalise_path(raw.get("path", ""))
    if not path:
        raise ValueError(f"{policy_path} guardrails.protected[{index}] requires path")

    severity_text = str(raw.get("severity", GuardrailSeverity.IMPORTANT.value)).lower()
    try:
        severity = GuardrailSeverity(severity_text)
    except ValueError as exc:
        raise ValueError(
            f"{policy_path} guardrails.protected[{index}] has unsupported "
            f"severity {severity_text!r}"
        ) from exc

    files = raw.get("files", ())
    if isinstance(files, str):
        file_patterns = (files,)
    elif isinstance(files, list):
        file_patterns = tuple(str(item) for item in files)
    elif files in (None, ()):
        file_patterns = ()
    else:
        raise ValueError(f"{policy_path} guardrails.protected[{index}].files is invalid")

    return ProtectedRule(
        rule_id=str(raw.get("id") or f"guardrail.{index + 1}"),
        severity=severity,
        language=language,
        path=path,
        message=str(raw.get("message") or f"Protected path {path} changed"),
        files=file_patterns,
    )


def _evaluate_policy_rules(
    diff: SemanticDiff,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    rules: Iterable[ProtectedRule],
) -> list[GuardrailViolation]:
    """Rust-authoritative (#91 A1.3b): rule matching + violation construction run in
    the Rust core (``evaluate_guardrail_rules_json``), which reuses the same
    semantic-path derivation. Python marshals the parsed policy + the diff's
    changed-node ids in and rebuilds GuardrailViolation objects out; the Python
    matching mirror (rule loop, ``_semantic_paths``, ``_node_value_summary``,
    ``_file_matches``) was deleted. Returns ``[]`` when the core is unavailable.
    """
    from intentdiff.rust_core import try_rust_evaluate_guardrail_rules

    request = {
        "language": diff.language,
        "old_filename": diff.old_filename,
        "new_filename": diff.new_filename,
        "old_tree": json.loads(old_tree.model_dump_json()),
        "new_tree": json.loads(new_tree.model_dump_json()),
        "changes": [
            {
                "old_node_id": change.old_node.id if change.old_node is not None else None,
                "new_node_id": change.new_node.id if change.new_node is not None else None,
            }
            for change in diff.changes
        ],
        "rules": [
            {
                "rule_id": rule.rule_id,
                "severity": rule.severity.value,
                "language": rule.language,
                "path": rule.path,
                "message": rule.message,
                "files": list(rule.files),
            }
            for rule in rules
        ],
    }
    violations = try_rust_evaluate_guardrail_rules(request)
    if not violations:
        return []
    return [
        GuardrailViolation(
            rule_id=item["rule_id"],
            severity=GuardrailSeverity(item["severity"]),
            file=item["file"],
            language=item["language"],
            semantic_path=item["semantic_path"],
            node_type=item.get("node_type", ""),
            old_node_id=item.get("old_node_id"),
            new_node_id=item.get("new_node_id"),
            position=item.get("position"),
            old_value=item.get("old_value", ""),
            new_value=item.get("new_value", ""),
            message=item.get("message", ""),
        )
        for item in violations
    ]


def _normalise_path(value: Any) -> str:
    text = str(value).strip().strip(".")
    return ".".join(_clean_path_part(part) for part in text.split(".") if part)


def _clean_path_part(value: str) -> str:
    text = str(value).strip().strip('"').strip("'")
    return text.lower()


def _is_policy_file(filename: str | None) -> bool:
    return Path(filename or "").name.lower() == GUARDRAIL_POLICY_FILENAME


def _find_policy_path(filename: str, explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return explicit_path if explicit_path.exists() else None

    starts: list[Path] = []
    raw = Path(filename)
    if filename and not filename.startswith("<"):
        starts.append(raw if raw.is_dir() else raw.parent)
    starts.append(Path.cwd())

    seen: set[Path] = set()
    for start in starts:
        try:
            current = start.resolve()
        except OSError:
            current = Path.cwd()
        for directory in (current, *current.parents):
            if directory in seen:
                continue
            seen.add(directory)
            candidate = directory / GUARDRAIL_POLICY_FILENAME
            if candidate.exists():
                return candidate
    return None
