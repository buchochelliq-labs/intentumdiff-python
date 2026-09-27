"""Project-level protected semantic change guardrails."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


from intentumdiff.analysis.diagnostics import DiagnosticsRecorder
from intentumdiff.analysis.keyed_profiles import KEYED_DATA_LANGUAGES
from intentumdiff.analysis.resource_profiles import RESOURCE_PROFILE_LANGUAGES
from intentumdiff.core.models import (
    DiffConfig,
    GuardrailSeverity,
    GuardrailViolation,
    SemanticDiff,
    SemanticNode,
)

GUARDRAIL_POLICY_FILENAME = "intentumdiff.yaml"
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
        # An EXPLICIT --policy is the caller asserting that file exists. Returning an empty
        # policy here is a fail-OPEN: the check then reports "Guardrail check passed" and
        # exits 0 with nothing on stderr, so a typo in the path is indistinguishable from a
        # clean run. For a gate whose documented purpose is stopping API keys changing
        # unreviewed, that is the worst possible default.
        #
        # Auto-discovery finding nothing is different and stays permissive: the user never
        # claimed a policy existed.
        if explicit_path is not None:
            raise FileNotFoundError(
                f"Guardrail policy not found: {explicit_path}. "
                "The check would otherwise pass with no rules loaded, which looks identical "
                "to a clean run."
            )
        return GuardrailPolicy(path=None, rules=())

    from intentumdiff.rust_core import _c_abi_call

    try:
        rules = _c_abi_call("parse_guardrail_policy", policy_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{policy_path}: {exc}") from exc
    return GuardrailPolicy(path=policy_path, rules=tuple(
        ProtectedRule(rule_id=rule["rule_id"], severity=GuardrailSeverity(rule["severity"]),
                      language=rule["language"], path=rule["path"], message=rule["message"],
                      files=tuple(rule["files"])) for rule in rules))


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

    from dataclasses import asdict
    from intentumdiff.rust_core import _c_abi_call

    policy = load_guardrail_policy(diff.new_filename or diff.old_filename,
                                   explicit_path=config.guardrail_policy_path)
    result = SemanticDiff.model_validate(_c_abi_call("apply_guardrail_policy", json.dumps({
        "diff": diff.model_dump(mode="json"),
        "old_tree": old_tree.model_dump(mode="json") if old_tree else None,
        "new_tree": new_tree.model_dump(mode="json") if new_tree else None,
        "old_source": old_source, "new_source": new_source,
        "rules": [asdict(rule) for rule in policy.rules],
    })))
    violations = result.guardrail_violations[len(diff.guardrail_violations):]

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

    return result


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
    ``_file_matches``) was deleted. Engine failures raise; only a successful empty result means no violations.
    """
    from intentumdiff.rust_core import try_rust_evaluate_guardrail_rules

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
