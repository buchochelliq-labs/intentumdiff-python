"""Project-level protected semantic change guardrails."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


from intentumdiff.analysis.diagnostics import DiagnosticsRecorder
from intentumdiff.core.models import (
    DiffConfig,
    GuardrailSeverity,
    GuardrailViolation,
    SemanticDiff,
    SemanticNode,
)

GUARDRAIL_POLICY_FILENAME = "intentumdiff.yaml"
# Which languages a protected rule may target is Rust policy: core's
# parse_guardrail_policy rejects unsupported languages (GUARDRAIL_RULE_LANGUAGES).


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
    # Both sides of a move can have a policy. Checking only the destination
    # silently releases protection on moves out of a guarded directory (#262).
    # An explicit policy is mandatory even before a certified Rust batch return
    # (#261); all YAML semantics and rule evaluation remain Rust-owned.
    policies = _load_relevant_guardrail_policies(
        filename, new_filename, config.guardrail_policy_path
    )
    return (
        _is_policy_file(filename)
        or _is_policy_file(new_filename)
        or any(policy.rules for policy in policies)
    )


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


def _load_relevant_guardrail_policies(
    old_filename: str,
    new_filename: str | None,
    explicit_path: Path | None,
) -> tuple[GuardrailPolicy, ...]:
    """Discover distinct host policy files for both sides; Rust owns their meaning."""
    if explicit_path is not None:
        # An explicit policy overrides auto-discovery and is required to exist.
        # load_guardrail_policy raises instead of manufacturing no rules (#261).
        return (load_guardrail_policy(new_filename or old_filename,
                                      explicit_path=explicit_path),)

    found: list[GuardrailPolicy] = []
    seen_paths: set[Path] = set()
    for filename in (old_filename, new_filename):
        if not filename:
            continue
        policy_path = _find_policy_path(filename, None)
        if policy_path is None or policy_path in seen_paths:
            continue
        seen_paths.add(policy_path)
        # Malformed, dangling or unreadable policies must fail closed even
        # when the other side of the move contains a valid policy.
        found.append(load_guardrail_policy(filename, explicit_path=policy_path))
    return tuple(found)


def apply_guardrails_to_diff(
    diff: SemanticDiff,
    *,
    old_tree: SemanticNode | None,
    new_tree: SemanticNode | None,
    old_source: str,
    new_source: str,
    config: DiffConfig,
    diagnostics: DiagnosticsRecorder | None = None,
    policy_filename: str | None = None,
    policy_old_filename: str | None = None,
) -> SemanticDiff:
    if not config.guardrails_enabled:
        return diff

    from dataclasses import asdict
    from intentumdiff.rust_core import _c_abi_call, _require_complete_batch_diff_dto

    # FileSource transports the physical identities for both sides, preserving
    # user-facing aliases. For cross-directory moves include both applicable
    # policy files; only Rust interprets and matches their rules (#262).
    policies = _load_relevant_guardrail_policies(
        policy_old_filename or diff.old_filename,
        policy_filename or diff.new_filename,
        config.guardrail_policy_path,
    )
    request = {
        "diff": diff.model_dump(mode="json"),
        "old_tree": old_tree.model_dump(mode="json") if old_tree else None,
        "new_tree": new_tree.model_dump(mode="json") if new_tree else None,
        "old_source": old_source, "new_source": new_source,
        "rules": [asdict(rule) for policy in policies for rule in policy.rules],
    }
    # Pure host-I/O provenance: Rust selects filename-scoped rules and
    # immutable policy-file warnings; public labels remain untouched (#260).
    if policy_filename is not None:
        request["guardrail_new_filename"] = policy_filename
    if policy_old_filename is not None:
        request["guardrail_old_filename"] = policy_old_filename
    response = _c_abi_call("apply_guardrail_policy", json.dumps(request))
    result = SemanticDiff.model_validate(_require_complete_batch_diff_dto(response))
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
        # The argument identifies a source *file*, even if its current checkout
        # entry is a symlink to a directory.  Do not follow that final component
        # into unrelated external policy directories for a historical Git diff.
        # For relative paths, the parent resolves against cwd already.  An
        # absolute path must never fall back to an unrelated process cwd.
        starts.append(raw.parent)
    else:
        # Synthetic/in-memory sources have no physical policy-search origin.
        starts.append(Path.cwd())

    seen: set[Path] = set()
    for start in starts:
        try:
            current = start.resolve()
        except OSError as exc:
            # A failed physical-path resolution is not permission to import
            # a policy from another repository's process working directory.
            # Report the host I/O failure instead of manufacturing a clean (or
            # foreign-policy) guardrail result.
            raise RuntimeError(
                f"Unable to resolve guardrail policy search path {start}: {exc}"
            ) from exc
        for directory in (current, *current.parents):
            if directory in seen:
                continue
            seen.add(directory)
            candidate = directory / GUARDRAIL_POLICY_FILENAME
            # A dangling symlink is an explicitly present but unusable policy.
            # Treating it as absent would silently disable protected-change checks.
            # The subsequent strict read must report the missing target instead.
            if candidate.exists() or candidate.is_symlink():
                return candidate
    return None
