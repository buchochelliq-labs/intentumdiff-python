"""
intentumdiff.analysis.invariances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Data-defined semantic invariance handling.

Rules are declared in packaged YAML and select a small allow-listed evaluator.
The YAML is data only: it never contains executable predicates, imports, or
arbitrary expressions.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal
from importlib import resources

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from intentumdiff.core.models import ChangeGroupKind

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


