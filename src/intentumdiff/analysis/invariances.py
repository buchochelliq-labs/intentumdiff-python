"""
intentumdiff.analysis.invariances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Data-defined semantic invariance handling.

Rules are declared in packaged YAML and select a small allow-listed evaluator.
The YAML is data only: it never contains executable predicates, imports, or
arbitrary expressions.
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from intentumdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeGroupKind,
    ChangeType,
    SemanticNode,
)

_ALLOWED_EVALUATORS = frozenset(
    {
        "css_color_equivalence",
        "style_only_shortcut_evidence",
        "integer_literal_equivalence",
        "string_literal_equivalence",
        "formatting_suppression_evidence",
        "parser_normalization",
        "number_literal_equivalence",
    }
)
_KNOWN_LANGUAGE_IDS = frozenset(
    {
        "abap",
        "adf",
        "asciidoc",
        "asm",
        "assemblyscript",
        "astro",
        "bash",
        "c",
        "clojure",
        "cpp",
        "csharp",
        "css",
        "dart",
        "databricks-workflow",
        "dax",
        "delphi",
        "gitignore",
        "dockerfile",
        "elixir",
        "freebasic",
        "generic",
        "go",
        "graphql",
        "groovy",
        "haskell",
        "hcl",
        "html",
        "java",
        "javascript",
        "json",
        "kotlin",
        "latex",
        "lua",
        "m",
        "markdown",
        "ini",
        "gomod",
        "make",
        "cmake",
        "proto",
        "toml",
        "mdx",
        "ocaml",
        "odin",
        "perl",
        "php",
        "plsql",
        "po",
        "postscript",
        "powershell",
        "puppet",
        "python",
        "qsharp",
        "r",
        "reasonml",
        "ruby",
        "rust",
        "sas",
        "scala",
        "scss",
        "sql",
        "squirrel",
        "svelte",
        "swift",
        "tsx",
        "tsql",
        "typescript",
        "vbnet",
        "vue",
        "wat",
        "xml",
        "yaml",
        "zig",
    }
)
_COLOR_TOKEN_RE = re.compile(
    r"""
    (?P<hex>\#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)
    |
    (?P<rgb>\brgb\(\s*
        (?P<r>\d{1,3})
        (?:
            \s*,\s*(?P<g_comma>\d{1,3})\s*,\s*(?P<b_comma>\d{1,3})
          |
            \s+(?P<g_space>\d{1,3})\s+(?P<b_space>\d{1,3})
        )
        \s*\))
    |
    (?P<name>\b[a-zA-Z]+\b)
    """,
    re.VERBOSE,
)

_CSS_NAMED_COLORS = {
    "black": (0, 0, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "fuchsia": (255, 0, 255),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "grey": (128, 128, 128),
    "lime": (0, 255, 0),
    "magenta": (255, 0, 255),
    "red": (255, 0, 0),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
}


class InvarianceRuleDefinition(BaseModel, frozen=True):
    """Validated declaration for one packaged invariance rule."""

    id: str = Field(min_length=1)
    languages: tuple[str, ...] = Field(min_length=1)
    status: Literal["implemented", "catalog"] = "implemented"
    evaluator: str | None = None
    node_types: tuple[str, ...] = Field(default_factory=tuple)
    group_kind: ChangeGroupKind = ChangeGroupKind.IGNORED_STYLE
    enabled: bool = True
    risk: Literal["green", "amber", "red"] = "amber"
    equivalence_kind: str = "canonical_value_equivalence"
    explanation: str = Field(min_length=1)
    examples: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    stage: str | None = None
    guarantee: str | None = None
    guards: tuple[str, ...] = Field(default_factory=tuple)
    source_refs: tuple[str, ...] = Field(default_factory=tuple)
    notes: str = ""

    @field_validator("evaluator")
    @classmethod
    def _known_evaluator(cls, value: str | None) -> str | None:
        if value is not None and value not in _ALLOWED_EVALUATORS:
            raise ValueError(f"unknown invariance evaluator: {value}")
        return value

    @field_validator("languages")
    @classmethod
    def _known_languages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - _KNOWN_LANGUAGE_IDS)
        if unknown:
            raise ValueError(f"unknown invariance languages: {unknown}")
        return value

    @field_validator("languages", "node_types", "guards", "source_refs", mode="before")
    @classmethod
    def _tuple_from_list(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return tuple()
        return tuple(value)

    @model_validator(mode="after")
    def _status_consistency(self) -> InvarianceRuleDefinition:
        if self.status == "implemented" and self.evaluator is None:
            raise ValueError("implemented invariance rules require an evaluator")
        if self.status == "catalog" and self.enabled:
            raise ValueError("catalog invariance rules must be disabled")
        return self


class InvarianceRuleSet(BaseModel, frozen=True):
    """Top-level packaged rule file shape."""

    version: int = Field(ge=1)
    rules: tuple[InvarianceRuleDefinition, ...] = Field(default_factory=tuple)

    @field_validator("rules", mode="before")
    @classmethod
    def _tuple_rules(cls, value: Any) -> tuple[Any, ...]:
        return tuple(value or ())

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> InvarianceRuleSet:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                duplicates.add(rule.id)
            seen.add(rule.id)
        if duplicates:
            raise ValueError(f"duplicate invariance rule ids: {sorted(duplicates)}")
        return self


@dataclass(frozen=True)
class InvarianceResult:
    changes: list[Change]
    change_groups: list[ChangeGroup] = field(default_factory=list)
    ignored_style_changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _ColorEvidence:
    old_label: str
    new_label: str
    canonical: str
    old_span: tuple[int, int]
    new_span: tuple[int, int]


@dataclass(frozen=True)
class _SourceEvidence:
    old_label: str
    new_label: str
    old_span: tuple[int, int]
    new_span: tuple[int, int]


@dataclass(frozen=True)
class _SourceLiteralEvidence(_SourceEvidence):
    canonical: str


@lru_cache(maxsize=1)
def load_builtin_invariance_rules() -> tuple[InvarianceRuleDefinition, ...]:
    """Load and validate packaged built-in invariance rules."""

    rule_path = resources.files("intentumdiff.invariances").joinpath("rules.yaml")
    raw = yaml.safe_load(rule_path.read_text(encoding="utf-8")) or {}
    return InvarianceRuleSet.model_validate(raw).rules


def builtin_invariance_rule(rule_id: str) -> InvarianceRuleDefinition | None:
    """Return a packaged invariance rule by id, if present."""

    for rule in load_builtin_invariance_rules():
        if rule.id == rule_id:
            return rule
    return None


def build_style_only_evidence(
    *,
    old_source: str,
    new_source: str,
    language: str,
    old_cst_json: str | None = None,
    new_cst_json: str | None = None,
    rules: tuple[InvarianceRuleDefinition, ...] | None = None,
) -> InvarianceResult:
    """Build source-span evidence for the fast style-only shortcut."""

    if old_source == new_source:
        return InvarianceResult(changes=[])

    active_rules = rules if rules is not None else load_builtin_invariance_rules()
    groups: list[ChangeGroup] = []
    ignored: list[dict[str, Any]] = []

    string_rule = _active_rule(
        active_rules,
        language=language,
        evaluator="string_literal_equivalence",
    )
    literal_spans: list[tuple[tuple[int, int], tuple[int, int]]] = []
    if string_rule is not None:
        literal_groups, literal_ignored = _source_string_literal_groups(
            old_source=old_source,
            new_source=new_source,
            language=language,
            rule=string_rule,
            index_space="style_only_shortcut",
            old_cst_json=old_cst_json,
            new_cst_json=new_cst_json,
        )
        groups.extend(literal_groups)
        ignored.extend(literal_ignored)
        literal_spans.extend(
            (
                tuple(group.metadata["old_span"]),  # type: ignore[arg-type]
                tuple(group.metadata["new_span"]),  # type: ignore[arg-type]
            )
            for group in literal_groups
        )

    shortcut_rule = _active_rule(
        active_rules,
        language=language,
        evaluator="style_only_shortcut_evidence",
    )
    if shortcut_rule is not None:
        generic_evidence = [
            evidence
            for evidence in _changed_source_evidence(old_source, new_source)
            if not _covered_by_literal_evidence(evidence, literal_spans)
        ]
        for occurrence, evidence in enumerate(generic_evidence):
            group, item = _source_group(
                evidence,
                rule=shortcut_rule,
                language=language,
                occurrence=occurrence,
                index_space="style_only_shortcut",
                confidence=0.75,
            )
            groups.append(group)
            ignored.append(item)

    return InvarianceResult(
        changes=[],
        change_groups=groups,
        ignored_style_changes=ignored,
    )


def build_zero_change_literal_evidence(
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_source: str,
    new_source: str,
    language: str,
    rules: tuple[InvarianceRuleDefinition, ...] | None = None,
) -> InvarianceResult:
    """Build literal equivalence evidence when refinement already removed all changes."""

    if old_source == new_source:
        return InvarianceResult(changes=[])

    active_rules = rules if rules is not None else load_builtin_invariance_rules()
    string_rule = _active_rule(
        active_rules,
        language=language,
        evaluator="string_literal_equivalence",
    )
    if string_rule is None:
        return InvarianceResult(changes=[])

    groups, ignored = _source_string_literal_groups(
        old_source=old_source,
        new_source=new_source,
        language=language,
        rule=string_rule,
        index_space="zero_change_source_equivalence",
        old_tree=old_tree,
        new_tree=new_tree,
    )
    return InvarianceResult(changes=[], change_groups=groups, ignored_style_changes=ignored)


def _active_rule(
    rules: tuple[InvarianceRuleDefinition, ...],
    *,
    language: str,
    evaluator: str,
) -> InvarianceRuleDefinition | None:
    for rule in rules:
        if (
            rule.status == "implemented"
            and rule.enabled
            and rule.evaluator == evaluator
            and language in rule.languages
        ):
            return rule
    return None


def apply_invariances(
    changes: list[Change],
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_source: str,
    new_source: str,
    language: str,
    rules: tuple[InvarianceRuleDefinition, ...] | None = None,
) -> InvarianceResult:
    """Remove review-level changes that are proven equivalent by invariance rules."""

    active_rules = rules if rules is not None else load_builtin_invariance_rules()
    current = list(changes)
    groups: list[ChangeGroup] = []
    ignored: list[dict[str, Any]] = []

    for rule in active_rules:
        if rule.status != "implemented" or not rule.enabled or language not in rule.languages:
            continue
        if rule.evaluator == "css_color_equivalence":
            current, rule_groups, rule_ignored = _apply_css_color_equivalence(
                current,
                old_tree=old_tree,
                new_tree=new_tree,
                old_source=old_source,
                new_source=new_source,
                rule=rule,
            )
            groups.extend(rule_groups)
            ignored.extend(rule_ignored)
        elif rule.evaluator == "integer_literal_equivalence":
            current, rule_groups, rule_ignored = _apply_literal_equivalence(
                current,
                old_source=old_source,
                new_source=new_source,
                language=language,
                rule=rule,
                literal_kind="integer",
            )
            groups.extend(rule_groups)
            ignored.extend(rule_ignored)
        elif rule.evaluator == "string_literal_equivalence":
            current, rule_groups, rule_ignored = _apply_literal_equivalence(
                current,
                old_source=old_source,
                new_source=new_source,
                language=language,
                rule=rule,
                literal_kind="string",
            )
            groups.extend(rule_groups)
            ignored.extend(rule_ignored)
        elif rule.evaluator == "number_literal_equivalence":
            current, rule_groups, rule_ignored = _apply_number_literal_equivalence(
                current,
                old_source=old_source,
                new_source=new_source,
                language=language,
                rule=rule,
            )
            groups.extend(rule_groups)
            ignored.extend(rule_ignored)
        elif rule.evaluator == "parser_normalization":
            pass

    return InvarianceResult(
        changes=current,
        change_groups=groups,
        ignored_style_changes=ignored,
    )


def _source_string_literal_groups(
    *,
    old_source: str,
    new_source: str,
    language: str,
    rule: InvarianceRuleDefinition,
    index_space: str,
    old_tree: SemanticNode | None = None,
    new_tree: SemanticNode | None = None,
    old_cst_json: str | None = None,
    new_cst_json: str | None = None,
) -> tuple[list[ChangeGroup], list[dict[str, Any]]]:
    evidence = _source_string_literal_evidence(
        old_source=old_source,
        new_source=new_source,
        language=language,
        old_tree=old_tree,
        new_tree=new_tree,
        old_cst_json=old_cst_json,
        new_cst_json=new_cst_json,
    )
    groups: list[ChangeGroup] = []
    ignored: list[dict[str, Any]] = []
    for occurrence, item in enumerate(evidence):
        group, ignored_item = _source_group(
            item,
            rule=rule,
            language=language,
            occurrence=occurrence,
            index_space=index_space,
            confidence=0.95,
            canonical=item.canonical,
        )
        groups.append(group)
        ignored.append(ignored_item)
    return groups, ignored


def _source_string_literal_evidence(
    *,
    old_source: str,
    new_source: str,
    language: str,
    old_tree: SemanticNode | None = None,
    new_tree: SemanticNode | None = None,
    old_cst_json: str | None = None,
    new_cst_json: str | None = None,
) -> list[_SourceLiteralEvidence]:
    old_spans = _string_literal_spans(
        old_source,
        tree=old_tree,
        cst_json=old_cst_json,
    )
    new_spans = _string_literal_spans(
        new_source,
        tree=new_tree,
        cst_json=new_cst_json,
    )
    if not old_spans or len(old_spans) != len(new_spans):
        return []

    result: list[_SourceLiteralEvidence] = []
    for old_text, old_span, new_text, new_span in zip(
        [item[0] for item in old_spans],
        [item[1] for item in old_spans],
        [item[0] for item in new_spans],
        [item[1] for item in new_spans],
        strict=True,
    ):
        if old_text == new_text:
            continue
        old_value = _canonical_string_literal(old_text, language)
        new_value = _canonical_string_literal(new_text, language)
        if old_value is None or new_value is None or old_value != new_value:
            continue
        result.append(
            _SourceLiteralEvidence(
                old_label=old_text,
                new_label=new_text,
                old_span=old_span,
                new_span=new_span,
                canonical=f"string({old_value})",
            )
        )
    return result


def _string_literal_spans(
    source: str,
    *,
    tree: SemanticNode | None = None,
    cst_json: str | None = None,
) -> list[tuple[str, tuple[int, int]]]:
    if tree is not None:
        line_offsets = _line_offsets(source)
        spans: list[tuple[str, tuple[int, int]]] = []
        for node in _all_semantic_nodes(tree):
            if not _is_string_node(node):
                continue
            start = _offset_from_line_col(
                line_offsets,
                node.position.start_line,
                node.position.start_col,
            )
            end = _offset_from_line_col(line_offsets, node.position.end_line, node.position.end_col)
            if start is None or end is None or start >= end:
                continue
            spans.append((source[start:end], (start, end)))
        return sorted(set(spans), key=lambda item: item[1])

    if cst_json is None:
        return []
    try:
        root = json.loads(cst_json)
    except (TypeError, ValueError):
        return []
    line_offsets = _line_offsets(source)
    spans = []
    for node in _walk_cst(root):
        if not _is_cst_string_container_type(str(node.get("type", ""))):
            continue
        start = _offset_from_line_col(
            line_offsets,
            int(node.get("start_line", -1)),
            int(node.get("start_col", -1)),
        )
        end = _offset_from_line_col(
            line_offsets,
            int(node.get("end_line", -1)),
            int(node.get("end_col", -1)),
        )
        if start is None or end is None or start >= end:
            continue
        spans.append((source[start:end], (start, end)))
    return sorted(set(spans), key=lambda item: item[1])


def _all_semantic_nodes(root: SemanticNode) -> list[SemanticNode]:
    return [root, *root.descendants()]


def _walk_cst(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node]
    for child in node.get("children", ()) or ():
        if isinstance(child, dict):
            nodes.extend(_walk_cst(child))
    return nodes


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", source):
        offsets.append(match.end())
    return offsets


def _offset_from_line_col(offsets: list[int], line: int, col: int) -> int | None:
    if line < 0 or col < 0 or line >= len(offsets):
        return None
    return offsets[line] + col


def _changed_source_evidence(old_source: str, new_source: str) -> list[_SourceEvidence]:
    result: list[_SourceEvidence] = []
    matcher = SequenceMatcher(None, old_source, new_source, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        result.append(
            _SourceEvidence(
                old_label=_source_label(old_source[old_start:old_end]),
                new_label=_source_label(new_source[new_start:new_end]),
                old_span=(old_start, old_end),
                new_span=(new_start, new_end),
            )
        )
    return result


def _source_label(value: str) -> str:
    if value == "":
        return "<empty>"
    if value.strip() == "":
        return "<whitespace>"
    label = value.replace("\r", "\\r").replace("\n", "\\n").strip()
    return label if len(label) <= 80 else f"{label[:77]}..."


def _covered_by_literal_evidence(
    evidence: _SourceEvidence,
    literal_spans: list[tuple[tuple[int, int], tuple[int, int]]],
) -> bool:
    for old_span, new_span in literal_spans:
        if _span_within(evidence.old_span, old_span) and _span_within(
            evidence.new_span,
            new_span,
        ):
            return True
    return False


def _span_within(inner: tuple[int, int], outer: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] <= inner[1] <= outer[1]


def _source_group(
    evidence: _SourceEvidence,
    *,
    rule: InvarianceRuleDefinition,
    language: str,
    occurrence: int,
    index_space: str,
    confidence: float,
    canonical: str | None = None,
) -> tuple[ChangeGroup, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "index_space": index_space,
        "reason": rule.explanation,
        "equivalence_kind": rule.equivalence_kind,
        "old_label": evidence.old_label,
        "new_label": evidence.new_label,
        "risk": rule.risk,
        "language": language,
        "occurrence": occurrence,
        "evidence_depth": "source_span",
        "old_span": list(evidence.old_span),
        "new_span": list(evidence.new_span),
    }
    if canonical is not None:
        metadata["canonical_old"] = canonical
        metadata["canonical_new"] = canonical

    group = ChangeGroup(
        kind=rule.group_kind,
        raw_change_indices=[],
        old_labels=[evidence.old_label],
        new_labels=[evidence.new_label],
        old_node_ids=[],
        new_node_ids=[],
        confidence=confidence,
        rule_id=rule.id,
        metadata=metadata,
    )
    return group, {"rule_id": rule.id, **metadata}


def _apply_css_color_equivalence(
    changes: list[Change],
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_source: str,
    new_source: str,
    rule: InvarianceRuleDefinition,
) -> tuple[list[Change], list[ChangeGroup], list[dict[str, Any]]]:
    if not changes:
        return changes, [], []

    old_normalized, old_tokens = _canonicalize_css_colors(old_source)
    new_normalized, new_tokens = _canonicalize_css_colors(new_source)
    if old_normalized != new_normalized:
        return changes, [], []

    evidence = _changed_color_evidence(old_tokens, new_tokens)
    if not evidence:
        return changes, [], []

    indices = list(range(len(changes)))
    old_labels = _labels_from_changes(changes, old=True)
    new_labels = _labels_from_changes(changes, old=False)
    old_node_ids = _node_ids_from_changes(changes, old=True) or _node_ids(old_tree)
    new_node_ids = _node_ids_from_changes(changes, old=False) or _node_ids(new_tree)

    groups: list[ChangeGroup] = []
    ignored: list[dict[str, Any]] = []
    for occurrence, item in enumerate(evidence):
        metadata = {
            "index_space": "invariance_input",
            "reason": rule.explanation,
            "equivalence_kind": rule.equivalence_kind,
            "canonical_old": item.canonical,
            "canonical_new": item.canonical,
            "old_label": item.old_label,
            "new_label": item.new_label,
            "risk": rule.risk,
            "language": "css",
            "occurrence": occurrence,
            "old_span": list(item.old_span),
            "new_span": list(item.new_span),
        }
        groups.append(
            ChangeGroup(
                kind=rule.group_kind,
                raw_change_indices=indices,
                old_labels=list(dict.fromkeys([*old_labels, item.old_label])),
                new_labels=list(dict.fromkeys([*new_labels, item.new_label])),
                old_node_ids=old_node_ids,
                new_node_ids=new_node_ids,
                confidence=1.0,
                rule_id=rule.id,
                metadata=metadata,
            )
        )
        ignored.append({"rule_id": rule.id, **metadata})

    return [], groups, ignored


def _apply_literal_equivalence(
    changes: list[Change],
    *,
    old_source: str,
    new_source: str,
    language: str,
    rule: InvarianceRuleDefinition,
    literal_kind: Literal["integer", "string"],
) -> tuple[list[Change], list[ChangeGroup], list[dict[str, Any]]]:
    if not changes:
        return changes, [], []

    kept: list[Change] = []
    groups: list[ChangeGroup] = []
    ignored: list[dict[str, Any]] = []

    for idx, change in enumerate(changes):
        evidence = _literal_equivalence_evidence(
            change,
            old_source=old_source,
            new_source=new_source,
            language=language,
            literal_kind=literal_kind,
        )
        if evidence is None:
            kept.append(change)
            continue

        old_node, new_node, old_label, new_label, canonical = evidence
        metadata = {
            "index_space": "invariance_input",
            "reason": rule.explanation,
            "equivalence_kind": rule.equivalence_kind,
            "canonical_old": canonical,
            "canonical_new": canonical,
            "old_label": old_label,
            "new_label": new_label,
            "old_node_type": old_node.node_type,
            "new_node_type": new_node.node_type,
            "risk": rule.risk,
            "language": language,
        }
        groups.append(
            ChangeGroup(
                kind=rule.group_kind,
                raw_change_indices=[idx],
                old_labels=[old_label],
                new_labels=[new_label],
                old_node_ids=[old_node.id],
                new_node_ids=[new_node.id],
                confidence=1.0 if literal_kind == "integer" else 0.95,
                rule_id=rule.id,
                metadata=metadata,
            )
        )
        ignored.append({"rule_id": rule.id, **metadata})

    return kept, groups, ignored


def _literal_equivalence_evidence(
    change: Change,
    *,
    old_source: str,
    new_source: str,
    language: str,
    literal_kind: Literal["integer", "string"],
) -> tuple[SemanticNode, SemanticNode, str, str, str] | None:
    if change.change_type != ChangeType.MODIFICATION:
        return None
    if change.old_node is None or change.new_node is None:
        return None
    old_node = change.old_node
    new_node = change.new_node
    if not (old_node.is_leaf() and new_node.is_leaf()):
        return None

    old_text = _node_source_text(old_source, old_node).strip()
    new_text = _node_source_text(new_source, new_node).strip()
    old_label = old_text or old_node.label
    new_label = new_text or new_node.label
    if old_label == new_label:
        return None

    if literal_kind == "integer":
        if not (_is_integer_node(old_node) and _is_integer_node(new_node)):
            return None
        old_value = _canonical_integer_literal(old_label)
        new_value = _canonical_integer_literal(new_label)
        if old_value is None or new_value is None or old_value != new_value:
            return None
        return old_node, new_node, old_label, new_label, f"int({old_value})"

    if not (_is_string_node(old_node) and _is_string_node(new_node)):
        return None
    old_value = _canonical_string_literal(old_label, language)
    new_value = _canonical_string_literal(new_label, language)
    if old_value is None or new_value is None or old_value != new_value:
        return None
    return old_node, new_node, old_label, new_label, f"string({old_value})"


def _node_source_text(source: str, node: SemanticNode) -> str:
    lines = source.splitlines()
    pos = node.position
    if pos.start_line < 0 or pos.start_line >= len(lines):
        return ""
    if pos.end_line < pos.start_line:
        return ""
    if pos.start_line == pos.end_line:
        return lines[pos.start_line][pos.start_col:pos.end_col]

    selected = [lines[pos.start_line][pos.start_col:]]
    for line_no in range(pos.start_line + 1, min(pos.end_line, len(lines))):
        selected.append(lines[line_no])
    if pos.end_line < len(lines):
        selected.append(lines[pos.end_line][:pos.end_col])
    return "\n".join(selected)


def _is_integer_node(node: SemanticNode) -> bool:
    node_type = node.node_type.lower()
    return (
        "integer" in node_type
        or node_type in {"int_literal", "numeric_literal", "number"}
    )


def _is_string_node(node: SemanticNode) -> bool:
    return _is_string_node_type(node.node_type)


def _is_string_node_type(node_type: str) -> bool:
    node_type = node_type.lower()
    return "string" in node_type or node_type in {"character_literal", "char_literal"}


def _is_cst_string_container_type(node_type: str) -> bool:
    node_type = node_type.lower()
    if node_type in {"string_start", "string_end", "string_content"}:
        return False
    return _is_string_node_type(node_type)


_INTEGER_LITERAL_RE = re.compile(
    r"""
    ^[+-]?
    (?:
        0[xX][0-9a-fA-F_]+
      | 0[oO][0-7_]+
      | 0[bB][01_]+
      | [0-9][0-9_]*
    )$
    """,
    re.VERBOSE,
)


def _canonical_integer_literal(value: str) -> int | None:
    stripped = value.strip()
    if not _INTEGER_LITERAL_RE.fullmatch(stripped):
        return None
    try:
        return int(stripped.replace("_", ""), 0)
    except ValueError:
        return None


def _apply_number_literal_equivalence(
    changes: list[Change],
    *,
    old_source: str,
    new_source: str,
    language: str,
    rule: InvarianceRuleDefinition,
) -> tuple[list[Change], list[ChangeGroup], list[dict[str, Any]]]:
    """Suppress changes where both number spellings parse to the same IEEE-754 double."""
    if not changes:
        return changes, [], []

    kept: list[Change] = []
    groups: list[ChangeGroup] = []
    ignored: list[dict[str, Any]] = []

    for idx, change in enumerate(changes):
        if change.change_type != ChangeType.MODIFICATION:
            kept.append(change)
            continue
        if change.old_node is None or change.new_node is None:
            kept.append(change)
            continue
        old_node = change.old_node
        new_node = change.new_node
        if not (old_node.is_leaf() and new_node.is_leaf()):
            kept.append(change)
            continue

        old_text = _node_source_text(old_source, old_node).strip()
        new_text = _node_source_text(new_source, new_node).strip()
        old_label = old_text or old_node.label
        new_label = new_text or new_node.label
        if old_label == new_label:
            kept.append(change)
            continue

        old_bits = _canonical_number_bits(old_label)
        new_bits = _canonical_number_bits(new_label)
        if old_bits is None or new_bits is None or old_bits != new_bits:
            kept.append(change)
            continue

        canonical = f"f64({old_label})"
        metadata = {
            "index_space": "invariance_input",
            "reason": rule.explanation,
            "equivalence_kind": rule.equivalence_kind,
            "canonical_old": canonical,
            "canonical_new": canonical,
            "old_label": old_label,
            "new_label": new_label,
            "old_node_type": old_node.node_type,
            "new_node_type": new_node.node_type,
            "risk": rule.risk,
            "language": language,
        }
        groups.append(
            ChangeGroup(
                kind=rule.group_kind,
                raw_change_indices=[idx],
                old_labels=[old_label],
                new_labels=[new_label],
                old_node_ids=[old_node.id],
                new_node_ids=[new_node.id],
                confidence=1.0,
                rule_id=rule.id,
                metadata=metadata,
            )
        )
        ignored.append({"rule_id": rule.id, **metadata})

    return kept, groups, ignored


def _canonical_number_bits(value: str) -> int | None:
    """Return the IEEE-754 bit pattern of a number literal, or None if unsafe.

    Rejects NaN payloads, infinities, and signed-zero mismatches so that
    only exact bit-identical doubles are considered equivalent.
    """
    import struct

    stripped = value.strip().replace("_", "")
    if not stripped or stripped in {"nan", "inf", "-inf", "infinity", "-infinity"}:
        return None
    try:
        f = float(stripped)
    except ValueError:
        return None
    # Reject NaN and infinities — their bit patterns are implementation-defined.
    if math.isnan(f) or math.isinf(f):
        return None
    bits = struct.unpack("<Q", struct.pack("<d", f))[0]
    # Treat +0.0 and -0.0 as distinct so sign changes are visible.
    return bits


def _canonical_string_literal(value: str, language: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    if "`" in stripped or "$" in stripped:
        return None

    if language == "python":
        prefix = stripped[: max(0, stripped.find(stripped[-1]))].lower()
        if any(ch in prefix for ch in "fbr"):
            return None
        try:
            decoded = ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            return None
        return decoded if isinstance(decoded, str) else None

    if language in {"javascript", "typescript", "tsx", "csharp"}:
        if stripped.startswith(("@", "$")):
            return None
        if len(stripped) < 2 or stripped[0] not in {"'", '"'} or stripped[-1] != stripped[0]:
            return None
        inner = stripped[1:-1]
        if stripped[0] == "'" and ("'" in inner or "\\" in inner):
            return None
        if stripped[0] == '"' and ('"' in inner):
            return None
        try:
            decoded = ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            if "\\" in inner:
                return None
            decoded = inner
        return decoded if isinstance(decoded, str) else None

    return None


def _canonicalize_css_colors(source: str) -> tuple[str, list[tuple[str, str, tuple[int, int]]]]:
    tokens: list[tuple[str, str, tuple[int, int]]] = []
    parts: list[str] = []
    last_end = 0

    for match in _COLOR_TOKEN_RE.finditer(source):
        original = match.group(0)
        canonical = _canonical_css_color_match(match)
        if canonical is None:
            continue
        parts.append(source[last_end:match.start()])
        parts.append(canonical)
        tokens.append((original, canonical, (match.start(), match.end())))
        last_end = match.end()

    parts.append(source[last_end:])
    return "".join(parts), tokens


def _canonical_css_color_match(match: re.Match[str]) -> str | None:
    if match.group("hex"):
        return _canonical_hex_color(match.group("hex"))
    if match.group("rgb"):
        g = match.group("g_comma") or match.group("g_space")
        b = match.group("b_comma") or match.group("b_space")
        return _canonical_rgb_color(match.group("r"), g, b)
    name = match.group("name")
    if name is None:
        return None
    rgb = _CSS_NAMED_COLORS.get(name.lower())
    if rgb is None:
        return None
    return _format_srgb(*rgb)


def _canonical_hex_color(value: str) -> str | None:
    raw = value[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return None
    try:
        red = int(raw[0:2], 16)
        green = int(raw[2:4], 16)
        blue = int(raw[4:6], 16)
    except ValueError:
        return None
    return _format_srgb(red, green, blue)


def _canonical_rgb_color(red: str | None, green: str | None, blue: str | None) -> str | None:
    if red is None or green is None or blue is None:
        return None
    try:
        channels = (int(red), int(green), int(blue))
    except ValueError:
        return None
    if any(channel < 0 or channel > 255 for channel in channels):
        return None
    return _format_srgb(*channels)


def _format_srgb(red: int, green: int, blue: int) -> str:
    return f"srgb({red},{green},{blue},1)"


def _changed_color_evidence(
    old_tokens: list[tuple[str, str, tuple[int, int]]],
    new_tokens: list[tuple[str, str, tuple[int, int]]],
) -> list[_ColorEvidence]:
    if len(old_tokens) != len(new_tokens):
        return []

    result: list[_ColorEvidence] = []
    for old, new in zip(old_tokens, new_tokens, strict=True):
        old_label, old_canonical, old_span = old
        new_label, new_canonical, new_span = new
        if old_canonical != new_canonical:
            return []
        if old_label != new_label:
            result.append(
                _ColorEvidence(
                    old_label=old_label,
                    new_label=new_label,
                    canonical=old_canonical,
                    old_span=old_span,
                    new_span=new_span,
                )
            )
    return result


def _labels_from_changes(changes: list[Change], *, old: bool) -> list[str]:
    labels: list[str] = []
    for change in changes:
        node = change.old_node if old else change.new_node
        labels.extend(_labels(node))
    return list(dict.fromkeys(labels))


def _node_ids_from_changes(changes: list[Change], *, old: bool) -> list[str]:
    node_ids: list[str] = []
    for change in changes:
        node = change.old_node if old else change.new_node
        node_ids.extend(_node_ids(node))
    return list(dict.fromkeys(node_ids))


def _labels(node: SemanticNode | None) -> list[str]:
    if node is None:
        return []
    return [item.label for item in [node, *node.descendants()] if item.label]


def _node_ids(node: SemanticNode | None) -> list[str]:
    if node is None:
        return []
    return [item.id for item in [node, *node.descendants()]]
