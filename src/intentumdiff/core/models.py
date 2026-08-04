"""
intentumdiff.core.models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

All public and internal data models.  Every model uses ``frozen=True`` so
instances are immutable and hashable — safe to use as dict keys / in sets.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum, IntEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import NamedTuple, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_serializer,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Fuel
# ---------------------------------------------------------------------------

FUEL_UNLIMITED: int = -1
"""Sentinel for ``DiffConfig.plugin_fuel`` that disables the Wasm fuel cap.

Passing this value means plugins run without an instruction budget.  Use with
care — a buggy plugin can spin forever.  The CLI accepts ``--fuel inf`` as a
human-readable alias.
"""


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class NodePosition(BaseModel, frozen=True):
    """Source-level span for a semantic node."""

    start_line: int = Field(ge=0)
    start_col: int = Field(ge=0)
    end_line: int = Field(ge=0)
    end_col: int = Field(ge=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> NodePosition:
        if (self.end_line, self.end_col) < (self.start_line, self.start_col):
            raise ValueError("end position must be >= start position")
        return self


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class DetectionResult(BaseModel, frozen=True):
    """
    Result of a content-based language detection query.

    Returned by :meth:`~intentumdiff.SemanticDiffer.detect_language` and
    :meth:`~intentumdiff.SemanticDiffer.detect_all`.
    """

    language: str = Field(min_length=1, description="Language ID (e.g. 'python', 'typescript').")
    grammar_id: str = Field(min_length=1, description="Grammar ID of the matching parser plugin.")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Relative confidence derived from parser priority order. "
            "1.0 for the highest-priority match, lower for subsequent matches."
        ),
    )


class LanguagePluginInfo(BaseModel, frozen=True):
    """Metadata for one parser plugin's handling of one language ID."""

    model_config = ConfigDict(populate_by_name=True)

    language_id: str = Field(alias="languageId", min_length=1)
    language_name: str = Field(alias="languageName", min_length=1)
    language_short_name: str = Field(alias="languageShortName", min_length=1)
    monaco_language: str = Field(alias="monacoLanguage", min_length=1)
    default_filename: str = Field(alias="defaultFilename", min_length=1)
    language_file_extensions: list[str] = Field(
        alias="languageFileExtensions", default_factory=list
    )
    author: str = ""
    plugin_version: str = Field(alias="pluginVersion", default="")
    last_updated: str = Field(alias="lastUpdated", default="")
    plugin_id: str = Field(alias="pluginId", min_length=1)
    grammar_id: str = Field(alias="grammarId", min_length=1)
    priority: int = 0
    is_trusted: bool = Field(alias="isTrusted", default=False)
    provenance: str = ""


class LanguageInfoGroup(BaseModel, frozen=True):
    """Grouped parser-plugin metadata for a language ID."""

    model_config = ConfigDict(populate_by_name=True)

    language: str = Field(min_length=1)
    selected_plugin_id: str = Field(alias="selectedPluginId", min_length=1)
    plugins: list[LanguagePluginInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Semantic tree
# ---------------------------------------------------------------------------


class NodeFacts(BaseModel, frozen=True):
    """
    Privacy-safe structural facts about a definition node (function/method/class),
    computed by the Rust parser from the full CST before pruning. Only counts,
    enums, and flags — never source text, identifiers, or literals — so they can
    describe a change's shape ("no parameters", "returns nothing", "no-op body")
    without revealing code. The host carries this through unchanged.
    """

    param_count: int | None = None
    #: ``"none"`` | ``"value"`` | ``"literal"`` (constant) | ``"unknown"``.
    returns: str | None = None
    #: The KIND of constant returned when ``returns == "literal"`` —
    #: ``"int"`` | ``"float"`` | ``"str"`` | ``"bool"`` | ``"none"`` | ``"mixed"``.
    #: The kind only, never the literal value (privacy-safe).
    return_kind: str | None = None
    #: ``"empty"`` | ``"stub"`` | ``"substantive"``.
    body: str | None = None
    #: True when the body has a bare call statement (a side effect / output). Flag only.
    side_effects: bool | None = None
    #: Behavior classification (#69-H) — control-flow shape, flags/enum only.
    has_conditional: bool | None = None
    has_loop: bool | None = None
    has_error_handling: bool | None = None
    #: The body raises/throws an exception. Flag only.
    throws: bool | None = None
    #: The body mutates state — an augmented assignment or an assignment to an
    #: attribute/subscript (``self.x = …``, ``a[i] = …``). Flag only, no target name.
    mutates: bool | None = None
    #: The body returns a freshly-built collection/object (list/dict/set/tuple/new/…).
    #: A factory signal. Flag only, never the constructed value.
    constructs: bool | None = None
    #: Whether a *substantive* body performs actual computation (operators/comprehensions/
    #: ternary) vs only calling out and returning. Explicit ``False`` is the #68 antidote —
    #: it lets the explainer say "performs no computation" instead of inventing it. Emitted
    #: only for substantive bodies; ``None`` for stub/empty bodies (nothing to assess).
    has_computation: bool | None = None
    #: ``"linear"`` | ``"branching"`` | ``"looping"`` — rollup of the above.
    control_shape: str | None = None
    #: Coarse "what kind of function" rollup — ``"accessor"`` | ``"transformer"`` |
    #: ``"validator"`` | ``"io"`` | ``"mutator"`` | ``"factory"``. Purpose signal only.
    behavior_category: str | None = None
    is_async: bool | None = None
    is_generator: bool | None = None
    decorator_count: int | None = None
    #: Class facts (#69 catalog D) — shape counts, from the class definition. Counts only.
    method_count: int | None = None
    field_count: int | None = None
    base_count: int | None = None
    #: Class kind, inferred from base classes (never the base NAME): an enumeration
    #: (Enum/IntEnum/…) or an exception (Exception/BaseException/`*Error`/`*Warning`). Flags only.
    is_enum: bool | None = None
    is_exception: bool | None = None
    #: Decorator semantics (#69 catalog C/D), inferred from decorator names but emitted as flags
    #: only (never the decorator name): read-only property, static/class method, abstract member,
    #: cached/memoized, and the dataclass class kind.
    is_property: bool | None = None
    is_staticmethod: bool | None = None
    is_classmethod: bool | None = None
    is_abstract: bool | None = None
    is_cached: bool | None = None
    is_dataclass: bool | None = None
    #: Param kinds (#69 catalog C) — counts/flags only, never a parameter name: params with a
    #: default (optional), keyword-only params (after `*`/`*args`), and variadic `*args`/`**kwargs`.
    default_count: int | None = None
    keyword_only_count: int | None = None
    has_variadic: bool | None = None
    has_kwargs: bool | None = None
    #: Coupling (#69-J) — outbound-call fan-out count (call sites, no callee names) and whether the
    #: function calls itself (recursion). Count/flag only.
    call_count: int | None = None
    recursive: bool | None = None


class SemanticNode(BaseModel, frozen=True):
    """
    A node in the language-specific semantic tree produced by a parser plugin.

    ``structural_hash`` is computed by the host (via the WIT host-import
    ``structural-hash``) so it is consistent across all plugins.
    """

    id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    label: str
    position: NodePosition
    structural_hash: str = Field(min_length=1)
    children: list[SemanticNode] = Field(default_factory=list)
    # Class name that directly owns this node (set by parsers for methods).
    # Used by PULL_UP / PUSH_DOWN refactoring detection.
    parent_type: str | None = None
    # Resolved type string from an LSP hover query (e.g. ``"int"``, ``"str | None"``).
    # ``None`` when no LSP server is configured or the server has no info.
    type_info: str | None = None
    # Privacy-safe structural facts for definition nodes (functions/methods/classes),
    # emitted by the Rust parser. Carried through unchanged to the extension.
    facts: NodeFacts | None = None

    # Memoisation caches — not part of the frozen public fields
    _height_cache: int | None = PrivateAttr(default=None)
    _descendants_cache: list[SemanticNode] | None = PrivateAttr(default=None)

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def height(self) -> int:
        if self._height_cache is None:
            h = 0 if self.is_leaf() else 1 + max(c.height() for c in self.children)
            object.__setattr__(self, "_height_cache", h)
        return self._height_cache  # type: ignore[return-value]

    def descendants(self) -> list[SemanticNode]:
        if self._descendants_cache is None:
            result: list[SemanticNode] = []
            for child in self.children:
                result.append(child)
                result.extend(child.descendants())
            object.__setattr__(self, "_descendants_cache", result)
        return self._descendants_cache  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Reference taxonomy
# ---------------------------------------------------------------------------


class ReferenceKind(str, Enum):
    """The kind of usage a ``ReferenceUsage`` represents."""

    CALL = "CALL"
    IMPORT = "IMPORT"
    TYPE_USAGE = "TYPE_USAGE"


# ---------------------------------------------------------------------------
# Change taxonomy
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Streaming change events
# ---------------------------------------------------------------------------


class ChangeStreamPhase(IntEnum):
    """Phase of the analysis pipeline that produced a streaming event."""

    STRUCTURAL = 1
    """After edit-script computation and move promotion (stage 11)."""
    REFINED = 2
    """After refactoring detection (stage 12)."""
    FINAL = 3
    """After classification and diff-analyzer passes (stages 13–13.5)."""


class ChangeType(str, Enum):
    ADDITION = "ADDITION"
    DELETION = "DELETION"
    MODIFICATION = "MODIFICATION"
    MOVE = "MOVE"
    REORDER = "REORDER"
    STYLE_ONLY = "STYLE_ONLY"
    REFACTORING = "REFACTORING"
    # Cross-file change types (populated by CommitDiffer / SemanticIndex)
    MOVE_TO_MODULE = "MOVE_TO_MODULE"
    CROSS_FILE_RENAME = "CROSS_FILE_RENAME"
    SPLIT_MODULE = "SPLIT_MODULE"


class GuardrailSeverity(str, Enum):
    """Severity for project-level protected semantic changes."""

    IMPORTANT = "important"
    IMMUTABLE = "immutable"


class RefactoringKind(str, Enum):
    RENAME_SYMBOL = "RENAME_SYMBOL"
    RENAME_CLASS = "RENAME_CLASS"
    RENAME_METHOD = "RENAME_METHOD"
    RENAME_VARIABLE = "RENAME_VARIABLE"
    EXTRACT_FUNCTION = "EXTRACT_FUNCTION"
    INLINE_FUNCTION = "INLINE_FUNCTION"
    CHANGE_SIGNATURE = "CHANGE_SIGNATURE"
    PULL_UP = "PULL_UP"
    PUSH_DOWN = "PUSH_DOWN"
    EXTRACT_CLASS = "EXTRACT_CLASS"
    INLINE_CLASS = "INLINE_CLASS"
    INLINE_VARIABLE = "INLINE_VARIABLE"
    EXTRACT_VARIABLE = "EXTRACT_VARIABLE"
    MERGE_METHOD = "MERGE_METHOD"
    INTRODUCE_PARAMETER_OBJECT = "INTRODUCE_PARAMETER_OBJECT"
    REPLACE_CONDITIONAL_WITH_POLYMORPHISM = "REPLACE_CONDITIONAL_WITH_POLYMORPHISM"
    REPLACE_LOOP_WITH_PIPELINE = "REPLACE_LOOP_WITH_PIPELINE"


class ChangeGroupKind(str, Enum):
    """Review-level grouping for related raw change events."""

    MOVED_CODE = "MOVED_CODE"
    REFACTORING = "REFACTORING"
    MEANINGFUL_CHANGE = "MEANINGFUL_CHANGE"
    IGNORED_STYLE = "IGNORED_STYLE"
    NOISE_SUPPRESSED = "NOISE_SUPPRESSED"


class Change(BaseModel, frozen=True):
    """A single semantic change between two versions of a file."""

    change_type: ChangeType | str
    old_node: SemanticNode | None = None
    new_node: SemanticNode | None = None
    refactoring_kind: RefactoringKind | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    description: str = ""
    # Inline character-level diff for leaf MODIFICATION changes (e.g. "foo[-Bar][+Baz]")
    text_diff: str | None = None
    # Sibling indices for REORDER changes
    old_index: int | None = None
    new_index: int | None = None

    @field_validator("change_type", mode="before")
    @classmethod
    def _normalise_change_type(cls, value: Any) -> ChangeType | str:
        if isinstance(value, ChangeType):
            return value
        if isinstance(value, str):
            try:
                return ChangeType(value)
            except ValueError:
                return value
        return value

    @model_validator(mode="after")
    def _node_consistency(self) -> Change:
        t = self.change_type
        if t == ChangeType.ADDITION and self.old_node is not None:
            raise ValueError("ADDITION must not have an old_node")
        if t == ChangeType.DELETION and self.new_node is not None:
            raise ValueError("DELETION must not have a new_node")
        if t == ChangeType.REFACTORING and self.refactoring_kind is None:
            raise ValueError("REFACTORING requires a refactoring_kind")
        return self


class ChangeGroup(BaseModel, frozen=True):
    """A higher-level semantic grouping backed by raw change evidence."""

    kind: ChangeGroupKind
    raw_change_indices: list[int] = Field(default_factory=list)
    old_labels: list[str] = Field(default_factory=list)
    new_labels: list[str] = Field(default_factory=list)
    old_node_ids: list[str] = Field(default_factory=list)
    new_node_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    rule_id: str = ""
    refactoring_kind: RefactoringKind | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalise_metadata(cls, v: Any) -> dict[str, Any]:
        return dict(v) if v is not None else {}

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, v: Mapping[str, Any]) -> MappingProxyType:
        if isinstance(v, MappingProxyType):
            return v
        return MappingProxyType(dict(v))

    @field_serializer("metadata")
    def _serialize_metadata(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return dict(v)


class ChangeStreamEvent(BaseModel, frozen=True):
    """A single event emitted by ``SemanticDiffer.diff_stream_progressive``.

    Events are ordered: STRUCTURAL (phase 1) events arrive first, followed by
    REFINED (phase 2) events that may revise or supersede them, and finally
    FINAL (phase 3) events from the classification and diff-analyzer passes.

    ``replaced_ids`` contains the ``old_node.id`` / ``new_node.id`` values of
    Phase-1 changes that this event supersedes.  When ``action="revise"`` the
    consumer should look up prior events by these IDs and replace them.  When
    ``action="remove"`` there is no replacement — the change was absorbed.
    """

    phase: ChangeStreamPhase
    action: Literal["add", "revise", "remove"]
    change: Change | None = None
    replaced_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff result
# ---------------------------------------------------------------------------


class SemanticDiff(BaseModel, frozen=True):
    """The complete semantic diff between two versions of a single file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    guardrail_violations: list[GuardrailViolation] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    change_groups: list[ChangeGroup] = Field(default_factory=list)
    old_filename: str
    new_filename: str
    language: str
    has_semantic_changes: bool = False
    is_style_only: bool = False
    parse_errors: list[str] = Field(default_factory=list)
    llm_summary: str = ""
    gitignore_excluded: bool = False
    # True when the parse step encountered errors and a token-level fallback was used
    is_fallback: bool = False
    # Describes where the change came from in the git lifecycle:
    # "unstaged" = saved but not staged, "staged" = git-add'd, "unpushed" = committed but not
    # yet pushed to the remote.  None for an ordinary commit-to-commit diff.
    staging_status: str | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalise_metadata(cls, v: Any) -> dict[str, Any]:
        return dict(v) if v is not None else {}

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, v: Mapping[str, Any]) -> MappingProxyType:
        if isinstance(v, MappingProxyType):
            return v
        return MappingProxyType(dict(v))

    @field_serializer("metadata")
    def _serialize_metadata(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return dict(v)

    @model_validator(mode="after")
    def _style_only_is_no_changes(self) -> SemanticDiff:
        if self.is_style_only and self.has_semantic_changes:
            raise ValueError(
                "is_style_only and has_semantic_changes cannot both be True"
            )
        return self

    @classmethod
    def style_only(
        cls,
        old_filename: str,
        new_filename: str,
        language: str,
        *,
        change_groups: list[ChangeGroup] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SemanticDiff:
        """Convenience constructor for the style-only shortcut."""
        return cls(
            old_filename=old_filename,
            new_filename=new_filename,
            language=language,
            change_groups=change_groups or [],
            has_semantic_changes=False,
            is_style_only=True,
            metadata=metadata or {},
        )

    @property
    def file_renamed(self) -> bool:
        """True when the file was renamed in-place (same directory, different basename)."""
        old = PurePosixPath(self.old_filename)
        new = PurePosixPath(self.new_filename)
        return old.name != new.name and old.parent == new.parent

    @property
    def file_moved(self) -> bool:
        """True when the file was moved to a different directory (with or without rename)."""
        return (
            PurePosixPath(self.old_filename).parent
            != PurePosixPath(self.new_filename).parent
        )


# ---------------------------------------------------------------------------
# Incremental parse hint
# ---------------------------------------------------------------------------


class EditDelta(BaseModel, frozen=True):
    """A single byte-range edit used to drive incremental tree-sitter parsing.

    Mirrors the arguments to ``tree_sitter.Tree.edit()`` so that a caller
    (e.g. ``LiveServer``) can pass precise edit information to the differ and
    avoid a full re-parse.

    All byte offsets are measured from the *start of the file*.  Points are
    ``(row, column)`` tuples using **0-based** row and column indices,
    consistent with the tree-sitter convention.
    """

    start_byte: int = Field(ge=0)
    old_end_byte: int = Field(ge=0)
    new_end_byte: int = Field(ge=0)
    start_point: tuple[int, int]
    old_end_point: tuple[int, int]
    new_end_point: tuple[int, int]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _native_rust_core_default() -> bool:
    """Return the default certified Rust-core policy.

    IntentumDiff ships the first-party Rust core in release wheels, so supported
    native paths should use it automatically.  The legacy
    ``INTENTUMDIFF_EXPERIMENTAL_RUST_CORE`` env var is still accepted for compatibility,
    while ``INTENTUMDIFF_RUST_CORE=0`` is the explicit fallback escape hatch.
    """

    raw = os.getenv("INTENTUMDIFF_RUST_CORE")
    if raw is None:
        raw = os.getenv("INTENTUMDIFF_EXPERIMENTAL_RUST_CORE")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on", "auto", "native"}


class Match(NamedTuple):
    """A matched (old, new) node pair from the tree matcher (Rust core)."""

    old_node: SemanticNode
    new_node: SemanticNode


Matching = list[Match]


class DiffConfig(BaseModel):
    """
    Configuration for the diffing pipeline.

    Unlike result models, this is NOT frozen — callers may update it freely
    before passing to ``SemanticDiffer``.
    """

    # GumTree tuning
    min_similarity: float = Field(default=0.5, ge=0.0, le=1.0)
    approx_move_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    min_height: int = Field(default=2, ge=0)

    # Feature flags
    ignore_style: bool = True
    detect_refactorings: bool = True
    diagnostics: bool = False
    profile_phases: bool = Field(
        default_factory=lambda: os.getenv("INTENTUMDIFF_PROFILE_PHASES", "").strip().lower()
        in {"1", "true", "yes", "on"},
    )
    """Attach opt-in phase timing metadata to diff results."""
    experimental_rust_core: bool = Field(
        default_factory=_native_rust_core_default,
    )
    """Use the certified Rust core for supported first-party native paths."""
    diagnostics_max_events: int = Field(default=500, ge=0)
    guardrails_enabled: bool = True
    guardrails_strict: bool = False
    guardrail_policy_path: Path | None = None

    # Test-only override for the GumTree matching stage. Production code
    # leaves this as ``None`` and the pipeline picks the matching engine
    # based on ``experimental_rust_core`` and the certified-surfaces check.
    # Truthiness tests set this to ``"rust"`` or ``"python"`` to force one
    # matcher so the dual-run matrix can certify both contracts. See
    # ``the retired NOISE_SUPPRESSION_RETUNE doc (git history)`` and the dual-run fixture in
    # ``tests/unit/test_wild_truthiness_regressions.py``.
    test_matching_engine: Literal["rust", "python"] | None = None

    # Extra CST node types to treat as trivia (merged with plugin defaults)
    extra_trivia_types: list[str] = Field(default_factory=list)

    # Plugin control
    allowed_plugins: list[str] | None = None
    strict_plugins: bool = False

    # Safety limits
    plugin_fuel: int = Field(default=100_000_000)
    """Wasm instruction budget per plugin call.  Use ``FUEL_UNLIMITED`` (``-1``)
    to remove the cap entirely."""

    @field_validator("plugin_fuel")
    @classmethod
    def _validate_plugin_fuel(cls, v: int) -> int:
        if v == FUEL_UNLIMITED:
            return v
        if v < 1:
            raise ValueError(
                "plugin_fuel must be a positive integer or FUEL_UNLIMITED (-1)"
            )
        return v
    max_nodes: int = Field(default=50_000, ge=1)
    # Maximum CST JSON size (in characters) accepted at the Wasm boundary.
    # Files whose serialised CST exceeds this are rejected with a clear error
    # rather than silently passing megabytes into the plugin sandbox.
    max_cst_bytes: int = Field(default=4 * 1024 * 1024, ge=1)  # 4 MB
    max_plugin_output_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    """Maximum text returned by a single Wasm plugin export before rejection."""

    # Compatibility field retained for older callers. Public parser hosting is
    # Rust/Wasm FullParse-only, so Python tree-sitter grammar factories are
    # accepted in config objects but are not used by the product path.
    extra_grammars: dict[str, Any] = Field(default_factory=dict)

    # Reference resolution
    resolve_references: bool = False

    # ── Caching / persistence ────────────────────────────────────────────────
    # Set ``cache_path`` to a directory to enable the SQLite parse + diff
    # cache.  The database is written to ``<cache_path>/cache.db``.
    # Set ``analytics_path`` to also record every diff in a DuckDB history
    # table at ``<analytics_path>/analytics.duckdb`` (requires ``duckdb``).
    cache_path: Path | None = Field(default=None)
    cache_ttl_days: int = Field(default=30, ge=1)
    cache_max_mb: int = Field(default=500, ge=1)
    analytics_path: Path | None = Field(default=None)

    # ── LSP type enrichment ──────────────────────────────────────────────────
    # Map from language name to ``LspServerConfig``.  When set, the indexer
    # connects to the corresponding server and attaches ``type_info`` to
    # ``SemanticNode`` leaves before diffing.  Requires the optional
    # ``lsprotocol`` dependency and a running language server.
    #
    # Example CLI usage::
    #
    #   intentumdiff index . --lsp python=localhost:2087
    lsp_servers: dict[str, Any] | None = Field(default=None)
    """Map of language → ``LspServerConfig``.  Type is ``Any`` to avoid a
    hard dependency on the optional ``lsprotocol`` package in core models."""

    lsp_timeout: float = Field(default=5.0, gt=0.0)
    """Per-request timeout (seconds) for LSP hover calls."""

    # ── Execution ─────────────────────────────────────────────────────────────
    parallel: int | bool = False
    """Fan-out commit-wide diffs across threads.

    - ``False`` (default): sequential execution.
    - ``True``: use ``os.cpu_count()`` threads.
    - Positive ``int``: explicit worker count.
    """

    @field_validator("parallel")
    @classmethod
    def _validate_parallel(cls, v: int | bool) -> int | bool:
        if isinstance(v, bool):
            return v
        if v < 0:
            raise ValueError("parallel must be False, True, or a positive integer")
        return v

    # ── Diff quality ─────────────────────────────────────────────────────────
    leaf_text_diff: bool = True
    """Annotate leaf-node MODIFICATION changes with a character-level inline diff
    stored in ``Change.text_diff``."""

    fallback_to_token_diff: bool = True
    """When the parser produces a tree containing ERROR nodes, fall back to a
    coarse token-level diff instead of running GumTree.  The resulting
    ``SemanticDiff`` will have ``is_fallback=True``."""

    stream_analysis: bool = False
    """Enable progressive per-phase streaming via ``diff_stream_progressive``.

    When ``True``, callers may iterate ``SemanticDiffer.diff_stream_progressive``
    to receive ``ChangeStreamEvent`` objects as each pipeline phase completes,
    rather than waiting for the entire pipeline to finish."""


# ---------------------------------------------------------------------------
# Cross-file semantic analysis models
# ---------------------------------------------------------------------------


class SymbolDefinition(BaseModel, frozen=True):
    """A symbol (function, class, etc.) defined in a specific file."""

    qualified_name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    start_line: int = Field(ge=0)
    start_col: int = Field(ge=0)
    end_line: int = Field(ge=0)
    end_col: int = Field(ge=0)
    language: str = ""


class ReferenceUsage(BaseModel, frozen=True):
    """A call-site, import, or type-annotation usage of a named symbol."""

    qualified_name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    reference_kind: ReferenceKind
    position: NodePosition
    language: str = ""
    # Dot-qualified name of the enclosing definition (e.g. "MyClass.method").
    # ``None`` for usages at module scope.
    enclosing_scope: str | None = None
    # Populated by ``SemanticIndex.find_references(name, resolve=True)`` when
    # exactly one matching definition is found in the same index.
    resolved_definition: SymbolDefinition | None = None


class CrossFileChange(BaseModel, frozen=True):
    """A semantic change that spans multiple files in a commit."""

    change_type: ChangeType | str
    symbol_name: str = Field(min_length=1)
    old_file: str
    new_file: str
    old_node_id: str | None = None
    new_node_id: str | None = None
    old_position: NodePosition | None = None
    new_position: NodePosition | None = None
    old_language: str = ""
    new_language: str = ""
    node_type: str = ""
    symbol_kind: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    description: str = ""


class GuardrailViolation(BaseModel, frozen=True):
    """A project policy rule matched a protected semantic change."""

    rule_id: str = Field(min_length=1)
    severity: GuardrailSeverity
    file: str = Field(min_length=1)
    language: str = ""
    semantic_path: str = Field(min_length=1)
    node_type: str = ""
    old_node_id: str | None = None
    new_node_id: str | None = None
    position: NodePosition | None = None
    old_value: str = ""
    new_value: str = ""
    message: str = ""


class GuardrailCheckResult(BaseModel, frozen=True):
    """CI-friendly summary of protected semantic guardrail evaluation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    violations: list[GuardrailViolation] = Field(default_factory=list)
    violation_count: int = 0
    immutable_count: int = 0
    important_count: int = 0
    checked_files: int = 0
    strict: bool = False
    passed: bool = True
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalise_metadata(cls, v: Any) -> dict[str, Any]:
        return dict(v) if v is not None else {}

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, v: Mapping[str, Any]) -> MappingProxyType:
        if isinstance(v, MappingProxyType):
            return v
        return MappingProxyType(dict(v))

    @field_serializer("metadata")
    def _serialize_metadata(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return dict(v)

    @model_validator(mode="after")
    def _derive_counts(self) -> GuardrailCheckResult:
        immutable_count = sum(
            violation.severity == GuardrailSeverity.IMMUTABLE
            for violation in self.violations
        )
        important_count = sum(
            violation.severity == GuardrailSeverity.IMPORTANT
            for violation in self.violations
        )
        object.__setattr__(self, "violation_count", len(self.violations))
        object.__setattr__(self, "immutable_count", immutable_count)
        object.__setattr__(self, "important_count", important_count)
        object.__setattr__(self, "passed", not (self.strict and immutable_count))
        return self


class CommitDiff(BaseModel, frozen=True):
    """
    The full semantic diff for a commit: per-file diffs plus cross-file
    changes detected by SemanticIndex comparison.
    """

    old_ref: str
    new_ref: str
    guardrail_violations: list[GuardrailViolation] = Field(default_factory=list)
    file_diffs: list[SemanticDiff] = Field(default_factory=list)
    cross_file_changes: list[CrossFileChange] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
