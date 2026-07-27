"""Rust core host adapter.

Release wheels ship the first-party ``intentdiff_rust_core`` PyO3 extension
inside the public ``intentdiff`` package, and supported native paths use it by
default.  The Python pipeline remains the fallback correctness path for
unsupported configs, missing native modules, and third-party/untrusted Wasm
plugin scenarios.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from intentdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeType,
    DiffConfig,
    Match,
    Matching,
    SemanticDiff,
    SemanticNode,
)

logger = logging.getLogger(__name__)

_COMPLETE_STATUS = "complete"
_BATCH_ENGINE = "rust_core_batch_v4"
_PYTHON_NATIVE_CERTIFICATION = "python_native_v4kb"
_CANONICAL_PYTHON_PLUGIN_ID = "intentdiff:python:python"
_PYTHON_PLUGIN_IDS = frozenset(
    {
        "",
        "python",
        "python-parser",
        "python_parser",
        _CANONICAL_PYTHON_PLUGIN_ID,
    }
)
_TREE_ENGINE = "rust_core_semantic_tree_v2_stage11"
# Language-agnostic engine label — emitted by ``diff_semantic_tree_json``
# (the migration-step-2 entrypoint that accepts any language label, not just
# Python). ``try_rust_core_tree_diff`` accepts either label so that
# pre-migration installs and post-migration installs both validate.
_TREE_ENGINE_V3 = "rust_core_semantic_tree_v3"
_SOURCE_ENGINE = "rust_core_sources_v3_stage11"


def _canonical_python_plugin_id(parser_plugin_id: str | None) -> str | None:
    return parser_plugin_id


def _canonicalize_file_plugin_ids(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for file in files:
        item = dict(file)
        if "parser_plugin_id" in item:
            item["parser_plugin_id"] = _canonical_python_plugin_id(item["parser_plugin_id"])
        if "parserPluginId" in item:
            item["parserPluginId"] = _canonical_python_plugin_id(item["parserPluginId"])
        normalized.append(item)
    return normalized


@dataclass(frozen=True)
class RustCoreAttempt:
    diff: SemanticDiff | None = None
    fallback_reason: str = ""
    backend_version: str = ""

    @property
    def used(self) -> bool:
        return self.diff is not None


@dataclass(frozen=True)
class RustCoreTreeAttempt:
    changes: list[Change] | None = None
    matching: Matching | None = None
    metadata: dict[str, Any] | None = None
    fallback_reason: str = ""
    backend_version: str = ""

    @property
    def used(self) -> bool:
        return self.changes is not None and self.matching is not None


@dataclass(frozen=True)
class RustCoreSourceAttempt:
    old_tree: SemanticNode | None = None
    new_tree: SemanticNode | None = None
    changes: list[Change] | None = None
    matching: Matching | None = None
    adaptive_fuel: int = 0
    metadata: dict[str, Any] | None = None
    fallback_reason: str = ""
    backend_version: str = ""

    @property
    def used(self) -> bool:
        return (
            self.old_tree is not None
            and self.new_tree is not None
            and self.changes is not None
            and self.matching is not None
        )


@dataclass(frozen=True)
class RustCoreBatchAttempt:
    diff: SemanticDiff | None = None
    fallback_reason: str = ""
    backend_version: str = ""

    @property
    def used(self) -> bool:
        return self.diff is not None


@dataclass(frozen=True)
class RustCoreBatchDiffsAttempt:
    diffs: dict[int, SemanticDiff] = field(default_factory=dict)
    fallback_reasons: dict[int, str] = field(default_factory=dict)
    batch_metadata: dict[str, Any] = field(default_factory=dict)
    adapter_phase_timings: list[dict[str, Any]] = field(default_factory=list)
    backend_version: str = ""
    fallback_reason: str = ""

    @property
    def used(self) -> bool:
        return bool(self.diffs)


@dataclass(frozen=True)
class RustCoreCandidateBatchAttempt:
    payload: dict[str, Any] | None = None
    fallback_reason: str = ""
    backend_version: str = ""

    @property
    def used(self) -> bool:
        return self.payload is not None


@dataclass(frozen=True)
class RustCoreCommitJsonAttempt:
    control: dict[str, Any] = field(default_factory=dict)
    commit_diff_json: bytes = b""
    adapter_phase_timings: list[dict[str, Any]] = field(default_factory=list)
    backend_version: str = ""
    fallback_reason: str = ""

    @property
    def used(self) -> bool:
        return bool(self.commit_diff_json)

    def to_model(self) -> Any:
        """Lazily validate the certified JSON into the public CommitDiff model."""
        from intentdiff.core.models import CommitDiff

        return CommitDiff.model_validate_json(self.commit_diff_json)


@dataclass(frozen=True)
class RustInvarianceResult:
    changes: list[Change]
    change_groups: list[ChangeGroup] = field(default_factory=list)
    ignored_style_changes: list[dict[str, Any]] = field(default_factory=list)


def rust_core_enabled(config: DiffConfig) -> bool:
    return bool(config.experimental_rust_core)


def try_rust_core_diff(
    *,
    old_content: str,
    new_content: str,
    old_filename: str,
    new_filename: str,
    language_hint: str | None,
    parser_plugin_id: str | None,
    config: DiffConfig,
) -> RustCoreAttempt:
    """Try the optional Rust core and return a fallback reason on failure."""
    if not rust_core_enabled(config):
        return RustCoreAttempt(fallback_reason="disabled")
    parser_plugin_id = _canonical_python_plugin_id(parser_plugin_id)
    if parser_plugin_id is not None and parser_plugin_id not in _PYTHON_PLUGIN_IDS:
        return RustCoreAttempt(fallback_reason="unsupported parser plugin")
    if language_hint not in (None, "", "python"):
        return RustCoreAttempt(fallback_reason="unsupported language hint")
    if not (old_filename.endswith((".py", ".pyi")) or new_filename.endswith((".py", ".pyi"))):
        return RustCoreAttempt(fallback_reason="unsupported filename")

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must never break diffing.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        supports = getattr(backend, "supports_language", None)
        if callable(supports) and not supports("python"):
            return RustCoreAttempt(fallback_reason="backend does not support python")
        backend_version = str(getattr(backend, "version", lambda: "")())
        payload = backend.diff_python_json(
            old_content,
            new_content,
            old_filename,
            new_filename,
            config.model_dump_json(),
        )
        diff = SemanticDiff.model_validate_json(payload)
        metadata = dict(diff.metadata)
        rust_metadata = dict(metadata.get("rust_core") or {})
        status = str(rust_metadata.get("status") or "")
        if status != _COMPLETE_STATUS:
            return RustCoreAttempt(
                fallback_reason=f"incomplete result status: {status or '<missing>'}",
                backend_version=backend_version,
            )
        rust_metadata.update(
            {
                "used": True,
                "backend_version": backend_version,
            }
        )
        metadata["rust_core"] = rust_metadata
        diff = diff.model_copy(update={"metadata": metadata})
        return RustCoreAttempt(diff=diff, backend_version=backend_version)
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core output rejected: %s", exc)
        return RustCoreAttempt(fallback_reason=f"invalid output: {exc}")
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core failed: %s", exc, exc_info=True)
        return RustCoreAttempt(fallback_reason=f"failed: {exc}")


def _pyo3_extension_module_name() -> str | None:
    """Return the importable name of the compiled PyO3 extension, or ``None``.

    Uses ``find_spec`` (never ``import``) and requires the spec's ``origin`` to be a compiled
    extension file (``.pyd``/``.so``), so it detects the real PyO3 build (dev / maturin) while
    NOT executing anything in the PyO3-free cdylib wheel — where ``intentdiff.intentdiff_rust_core``
    is a package (its ``__init__`` is the unused cffi wrapper), not an extension module.
    """
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415

    ext_suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    # Cover both layouts: the .pyd bundled inside the `intentdiff` package (release pyo3 wheel:
    # `intentdiff.intentdiff_rust_core`) and the standalone maturin package whose extension is the
    # like-named submodule (dev: `intentdiff_rust_core.intentdiff_rust_core`).
    for module_name in (
        "intentdiff.intentdiff_rust_core",
        "intentdiff_rust_core.intentdiff_rust_core",
        "intentdiff_rust_core",
    ):
        try:
            spec = importlib.util.find_spec(module_name)
        except (ModuleNotFoundError, ValueError, ImportError):
            continue
        if spec is not None and spec.origin and spec.origin.endswith(ext_suffixes):
            return module_name
    return None


def _load_backend() -> Any:
    # Backend selection (Phase B / #82). The C ABI (`intentdiff_call`) is the language-agnostic
    # boundary; the PyO3 extension is the transitional in-process fast path. Order:
    #   1. explicit override — `INTENTDIFF_RUST_CORE_CTYPES=1` forces the pure-ctypes proxy,
    #      `INTENTDIFF_RUST_CORE_PYO3=1` forces PyO3.
    #   2. auto — PyO3 when a compiled extension module is present (dev/maturin build; direct calls
    #      are faster); otherwise the pure-ctypes proxy over the cdylib (the PyO3-free wheel).
    if os.getenv("INTENTDIFF_RUST_CORE_CTYPES", "").strip() == "1":
        return _ctypes_backend()
    force_pyo3 = os.getenv("INTENTDIFF_RUST_CORE_PYO3", "").strip() == "1"
    module_name = _pyo3_extension_module_name()
    if module_name is not None:
        return importlib.import_module(module_name)
    if force_pyo3:
        raise ModuleNotFoundError("intentdiff_rust_core PyO3 extension (INTENTDIFF_RUST_CORE_PYO3=1)")
    return _ctypes_backend()


# ── Stateless C-ABI accessor (intentdiff_call) ───────────────────────────────────────────────
# The compiled core exports the language-agnostic C ABI (`intentdiff_call`/`intentdiff_free`,
# ungated) alongside the PyO3 module, so the SAME artifact serves both. Handlers reachable ONLY
# over the C ABI (the stateless `cache_*` store surface — there is no PyO3 pyfunction for them)
# are driven through here. This is also the seam the full ctypes binding (B.5) grows from as the
# remaining stateless functions migrate off PyO3.
_C_ABI_LIB: Any = None


def _extension_path() -> str:
    """Filesystem path to the compiled core that exports the C ABI (`intentdiff_call`).

    Handles both shapes: the PyO3 extension (dev / maturin build — a ``.pyd``/``.so`` module) and
    the bare cdylib shipped as a data file in the PyO3-free wheel
    (``intentdiff/intentdiff_rust_core/intentdiff_rust_core.{dll,so,dylib}``). Uses ``find_spec``
    throughout — never ``import`` — so the cdylib wheel's cffi wrapper ``__init__`` never runs.
    """
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415

    ext_suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    lib_suffixes = (".dll", ".so", ".dylib")
    # 1. The compiled PyO3 extension module — its origin IS the shared object.
    name = _pyo3_extension_module_name()
    if name is not None:
        spec = importlib.util.find_spec(name)
        if spec is not None and spec.origin:
            return spec.origin
    # 2. The bare cdylib: search the intentdiff_rust_core subpackage dir (and the intentdiff package
    #    dir) for `*intentdiff_rust_core*.{dll,so,dylib}`, via find_spec locations.
    search_dirs: list[str] = []
    for package_name in ("intentdiff.intentdiff_rust_core", "intentdiff_rust_core", "intentdiff"):
        try:
            spec = importlib.util.find_spec(package_name)
        except (ModuleNotFoundError, ValueError):
            continue
        if spec is None:
            continue
        locations = list(spec.submodule_search_locations or [])
        if locations:
            search_dirs.extend(locations)
        elif spec.origin and spec.origin not in ("built-in", "namespace"):
            search_dirs.append(os.path.dirname(spec.origin))
    for directory in search_dirs:
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for entry in entries:
            if "intentdiff_rust_core" in entry and (
                entry.endswith(lib_suffixes) or entry.endswith(ext_suffixes)
            ):
                return os.path.join(directory, entry)
    raise RuntimeError("intentdiff_rust_core compiled core not found for the C ABI")


def _c_abi_lib() -> Any:
    global _C_ABI_LIB
    if _C_ABI_LIB is None:
        import ctypes  # noqa: PLC0415

        lib = ctypes.CDLL(_extension_path())
        lib.intentdiff_call.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.intentdiff_call.restype = ctypes.c_void_p
        lib.intentdiff_free.argtypes = [ctypes.c_void_p]
        lib.intentdiff_free.restype = None
        _C_ABI_LIB = lib
    return _C_ABI_LIB


def _c_abi_call(name: str, *args: Any) -> Any:
    """Invoke a C-ABI handler by name; return its ``result`` or re-raise the classified error.

    `error_type` maps to the exception the PyO3 wrappers raised: ``not_found`` →
    ``FileNotFoundError`` (the git readers' only non-``ValueError``), everything else →
    ``ValueError``. Panics surface as ``RuntimeError``.
    """
    import ctypes  # noqa: PLC0415

    lib = _c_abi_lib()
    # `bytes` args cross the ABI as a JSON array of byte values (the C ABI's `Vec<u8>` shape —
    # e.g. detect_content_type's head slice), since raw bytes are not JSON-serialisable.
    payload = [list(a) if isinstance(a, (bytes, bytearray)) else a for a in args]
    ptr = lib.intentdiff_call(
        name.encode("utf-8"), json.dumps(payload).encode("utf-8")
    )
    if not ptr:
        raise RuntimeError(f"intentdiff_call({name!r}) returned NULL (allocation failure)")
    try:
        raw = ctypes.cast(ptr, ctypes.c_char_p).value
    finally:
        lib.intentdiff_free(ptr)
    envelope = json.loads((raw or b"").decode("utf-8"))
    if envelope.get("ok"):
        return envelope.get("result")
    message = envelope.get("error", "engine error")
    kind = envelope.get("error_type", "value_error")
    if kind == "not_found":
        raise FileNotFoundError(message)
    if kind == "internal":
        raise RuntimeError(message)
    raise ValueError(message)


class _CtypesBackend:
    """Pure-ctypes stand-in for the PyO3 module (#B.5): every attribute is a function that routes
    through the C ABI. `getattr(backend, fn)` returns a callable — the code's ``getattr(..., None)``
    guards always take the present-branch, which is correct since the C ABI covers the full
    stateless pyfunction surface.

    Naming: the C-ABI handler names dropped the Python-transport ``_json`` suffix (``finalize_review``
    for the pyfunction ``finalize_review_json``), so a trailing ``_json`` is stripped — except the two
    commit handlers, which keep the suffix AND return the ``(control_json, commit_bytes)`` binary tuple
    the callers expect (the C ABI marshals it as a ``{control, commit_diff_json}`` envelope).

    Result encoding: ``_c_abi_call`` returns the *parsed* ``result``. The PyO3 pyfunctions were
    heterogeneous — some returned native values used directly (``supports_language`` -> bool,
    ``validate_dep_hashes`` -> list, ``version`` -> str), others returned a JSON *string* the caller
    ``json.loads``/``model_validate_json`` (``changed_sources_json``, ``diff_batch``, every ``*_json``
    review/diff). The parsed value is already correct for the former; the latter are re-serialized
    with ``json.dumps`` so the caller's parse still works. ``_RAW_HANDLERS`` is the (small) native set.
    """

    _COMMIT_TUPLE = frozenset({"diff_batch_commit_json", "diff_working_tree_python_commit_json"})
    _RAW_HANDLERS = frozenset({
        "version",
        "supports_language",
        "git_repo_toplevel",
        "cache_make_key",
        "cache_parse_key",
        "cache_diff_key",
        "cache_hover_map_key",
        "validate_registry_ref",
        "validate_dep_hashes",
        "find_intentdiff_config_path",
        "vcs_backend_resolve_root",
        "vcs_backend_merge_base",
    })

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if name in self._COMMIT_TUPLE:
            def _commit(request_json: str, _name: str = name) -> tuple[str, bytes | None]:
                envelope = _c_abi_call(_name, request_json)
                payload = envelope.get("commit_diff_json")
                return (
                    json.dumps(envelope.get("control")),
                    payload.encode("utf-8") if payload is not None else None,
                )

            return _commit
        handler = name[:-5] if name.endswith("_json") else name
        if handler == "vcs_backend_get_blob":
            # The C ABI wraps blob content in {"content": …} so JSON-valued content is not
            # re-parsed by the envelope; the caller wants the bare string.
            def _blob(*args: Any) -> str:
                return _c_abi_call("vcs_backend_get_blob", *args)["content"]

            return _blob
        if handler == "register_user_xml_dialects":
            # The pyfunction takes a JSON *string*; the C-ABI handler takes the parsed array.
            def _register(dialects_json: str) -> Any:
                return _c_abi_call("register_user_xml_dialects", json.loads(dialects_json))

            return _register
        raw = handler in self._RAW_HANDLERS

        def _call(*args: Any, _handler: str = handler, _raw: bool = raw) -> Any:
            result = _c_abi_call(_handler, *args)
            return result if _raw else json.dumps(result)

        return _call


_CTYPES_BACKEND: _CtypesBackend | None = None


def _ctypes_backend() -> _CtypesBackend:
    global _CTYPES_BACKEND
    if _CTYPES_BACKEND is None:
        _CTYPES_BACKEND = _CtypesBackend()
    return _CTYPES_BACKEND


def enrich_node_facts(tree: SemanticNode) -> SemanticNode:
    """Fill language-agnostic NodeFacts (issue #70) on a Wasm-parsed tree via the Rust core.

    Non-Python parsers emit ``facts: None``; this asks the Rust core to derive the same
    privacy-safe structural facts (param_count, returns/return_kind, side_effects) from the
    SemanticNode tree so every language gives the intent explainer structural signal. Idempotent —
    nodes that already carry facts (the native Python path) are untouched. On any failure (backend
    or function unavailable) the tree is returned unchanged: enrichment must never break a diff.
    """
    try:
        backend = _load_backend()
        enrich = getattr(backend, "enrich_node_facts_json", None)
        if enrich is None:
            return tree
        return SemanticNode.model_validate_json(enrich(tree.model_dump_json()))
    except Exception:  # noqa: BLE001 — optional enrichment must degrade gracefully
        return tree


def apply_invariances(
    changes: list[Change],
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_source: str,
    new_source: str,
    language: str,
) -> RustInvarianceResult:
    """Apply built-in invariance rules in the Rust engine."""

    return _run_invariance_request(
        {
            "mode": "apply",
            "changes": [change.model_dump(mode="json") for change in changes],
            "old_tree": old_tree.model_dump(mode="json"),
            "new_tree": new_tree.model_dump(mode="json"),
            "old_source": old_source,
            "new_source": new_source,
            "language": language,
        }
    )


def build_style_only_evidence(
    *,
    old_source: str,
    new_source: str,
    language: str,
    old_cst_json: str | None = None,
    new_cst_json: str | None = None,
) -> RustInvarianceResult:
    """Build source-span style-only evidence in the Rust engine."""

    return _run_invariance_request(
        {
            "mode": "style_only",
            "old_source": old_source,
            "new_source": new_source,
            "language": language,
            "old_cst_json": old_cst_json,
            "new_cst_json": new_cst_json,
        }
    )


def build_zero_change_literal_evidence(
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_source: str,
    new_source: str,
    language: str,
) -> RustInvarianceResult:
    """Build zero-change literal evidence in the Rust engine."""

    return _run_invariance_request(
        {
            "mode": "zero_change_literal",
            "old_tree": old_tree.model_dump(mode="json"),
            "new_tree": new_tree.model_dump(mode="json"),
            "old_source": old_source,
            "new_source": new_source,
            "language": language,
        }
    )


def build_scope_trails(
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    changes: list[Change],
) -> dict[str, Any]:
    """Build hunk scope breadcrumb metadata in the Rust engine."""

    try:
        backend = _load_backend()
        scope_fn = getattr(backend, "scope_trails_json", None)
        if not callable(scope_fn):
            raise RuntimeError("backend does not expose scope_trails_json")
        payload = scope_fn(
            json.dumps(
                {
                    "old_tree": old_tree.model_dump(mode="json"),
                    "new_tree": new_tree.model_dump(mode="json"),
                    "changes": [change.model_dump(mode="json") for change in changes],
                }
            )
        )
        data = json.loads(payload)
    except Exception as exc:  # noqa: BLE001 - scope computation belongs to Rust.
        raise RuntimeError(f"Rust scope trail engine unavailable: {exc}") from exc

    scope_trails = data.get("scope_trails") if isinstance(data, dict) else None
    return dict(scope_trails) if isinstance(scope_trails, dict) else {}


def try_rust_generic_text_review(
    old_source: str,
    new_source: str,
    raw_change_count: int,
) -> tuple[list[Change], list[ChangeGroup]] | None:
    """Run the Rust generic-text review stage; None means fall back to Python.

    Engine-side port of the generic text presentation (issue #35): line diff,
    relocated-line netting, blank symmetry, inline char detail, and the
    presentation.generic_text_diff suppression audit group.
    """
    try:
        backend = _load_backend()
        review_fn = getattr(backend, "generic_text_review_json", None)
        if not callable(review_fn):
            return None
        data = json.loads(review_fn(old_source, new_source, raw_change_count))
        if not isinstance(data, dict) or not data.get("used"):
            return None
        changes = [Change.model_validate(item) for item in data.get("changes") or []]
        groups: list[ChangeGroup] = []
        group = data.get("group")
        if group:
            groups.append(ChangeGroup.model_validate(group))
        return changes, groups
    except Exception as exc:  # noqa: BLE001 - strangler boundary must fall back.
        logger.debug("Rust generic-text review unavailable: %s", exc, exc_info=True)
        return None


def try_rust_markdown_section_review(
    old_source: str,
    new_source: str,
) -> dict[str, object] | None:
    """Run the Rust markdown section review (moves + heading renames); None -> Python.

    Engine-side port of the markdown post-presentation rules (issue #36): section
    identity by content hash, LIS insertion-shift discrimination for moves, and
    unique-body-hash heading renames.
    """
    try:
        backend = _load_backend()
        review_fn = getattr(backend, "markdown_section_review_json", None)
        if not callable(review_fn):
            return None
        data = json.loads(review_fn(old_source, new_source))
        if not isinstance(data, dict) or not data.get("used"):
            return None
        return {
            "moves": [Change.model_validate(item) for item in data.get("moves") or []],
            "moved_labels": set(data.get("moved_labels") or []),
            "move_group": (
                ChangeGroup.model_validate(data["move_group"]) if data.get("move_group") else None
            ),
            "renames": [Change.model_validate(item) for item in data.get("renames") or []],
            "old_heading_lines": set(data.get("old_heading_lines") or []),
            "new_heading_lines": set(data.get("new_heading_lines") or []),
            "rename_group": (
                ChangeGroup.model_validate(data["rename_group"])
                if data.get("rename_group")
                else None
            ),
        }
    except Exception as exc:  # noqa: BLE001 - strangler boundary must fall back.
        logger.debug("Rust markdown section review unavailable: %s", exc, exc_info=True)
        return None


def try_rust_collect_hover_targets(
    root: SemanticNode,
) -> list[tuple[str, int, int]] | None:
    """Collect LSP hover targets in the Rust core (issue 100 S2); None -> Python walk.

    Returns ``(result_node_id, line, col)`` triples mirroring
    ``TypeEnricher._collect_hover_targets``: name-type leaves hover at their own
    position; declaration/parameter nodes hover at their first name-leaf but store
    under the declaration node's id.
    """
    try:
        backend = _load_backend()
        collect_fn = getattr(backend, "lsp_collect_hover_targets_json", None)
        if not callable(collect_fn):
            return None
        data = json.loads(collect_fn(root.model_dump_json()))
        if not isinstance(data, list):
            return None
        return [
            (str(item["id"]), int(item["line"]), int(item["col"]))
            for item in data
            if isinstance(item, dict)
        ]
    except Exception as exc:  # noqa: BLE001 - strangler boundary must fall back.
        logger.debug("Rust hover-target collection unavailable: %s", exc, exc_info=True)
        return None


def try_rust_finalize_review(
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_source: str,
    new_source: str,
    language: str,
    config: DiffConfig,
    collect_trace: bool = False,
) -> dict[str, Any] | None:
    """Route the per-stage draft finalization through the Rust core (issue #57).

    Runs the SAME refine+finalize pipeline as the certified batch from the provided
    semantic trees. None -> caller stays on the Python transitional path.
    """
    try:
        backend = _load_backend()
        finalize_fn = getattr(backend, "finalize_review_json", None)
        if not callable(finalize_fn):
            return None
        config_json = json.dumps(
            {
                "min_height": config.min_height,
                "min_similarity": config.min_similarity,
                "max_nodes": config.max_nodes,
                "collect_trace": collect_trace,
            }
        )
        data = json.loads(
            finalize_fn(
                old_tree.model_dump_json(),
                new_tree.model_dump_json(),
                old_source,
                new_source,
                language,
                config_json,
            )
        )
        if not isinstance(data, dict) or not data.get("used"):
            return None
        return {
            "changes": [Change.model_validate(item) for item in data.get("changes") or []],
            "change_groups": [
                ChangeGroup.model_validate(item) for item in data.get("change_groups") or []
            ],
            "is_style_only": bool(data.get("is_style_only")),
            "no_surviving_changes": bool(data.get("no_surviving_changes")),
            "ignored_style_changes": [
                dict(item)
                for item in data.get("ignored_style_changes") or []
                if isinstance(item, dict)
            ],
            "trace": [
                dict(item)
                for item in data.get("trace") or []
                if isinstance(item, dict)
            ],
        }
    except Exception as exc:  # noqa: BLE001 - strangler boundary must fall back.
        logger.debug("Rust finalize review unavailable: %s", exc, exc_info=True)
        return None


def try_rust_profile_label_enrichment(
    tree: SemanticNode,
    source: str,
    language: str,
    identity_fields: tuple[str, ...] = (),
) -> SemanticNode | None:
    """Profile-label enrichment in the Rust core (issue #57 profile-enrichment port).

    Returns the enriched tree, or None when the backend is unavailable (caller
    falls back to the python profile enrichment until every family is ported).
    """
    try:
        backend = _load_backend()
        enrich_fn = getattr(backend, "enrich_profile_labels_json", None)
        if not callable(enrich_fn):
            return None
        return SemanticNode.model_validate_json(
            enrich_fn(tree.model_dump_json(), source, language, list(identity_fields))
        )
    except Exception as exc:  # noqa: BLE001 - strangler boundary must fall back.
        logger.debug("Rust profile enrichment unavailable: %s", exc, exc_info=True)
        return None


def try_rust_build_symbol_table(files_json: str) -> str | None:
    """Symbol-table extraction in the Rust core (index-engine-lib, #91).

    *files_json* is a JSON array of ``{filename, language, tree}`` entries.
    Returns the SymbolTable JSON, or None when the backend/entrypoint is
    unavailable or the core reports an ``{"error": ...}`` envelope. This is the
    Rust-authoritative replacement for the retired Python symbol extraction and
    the index-engine Wasm-adapter round-trip.
    """
    try:
        backend = _load_backend()
        build_fn = getattr(backend, "build_symbol_table_json", None)
        if not callable(build_fn):
            return None
        result = build_fn(files_json)
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "error" in parsed:
            logger.debug("Rust build_symbol_table error: %s", parsed["error"])
            return None
        return result
    except Exception as exc:  # noqa: BLE001 - engine boundary must degrade, not crash.
        logger.debug("Rust build_symbol_table unavailable: %s", exc, exc_info=True)
        return None


def try_rust_build_reference_table(files_json: str) -> str | None:
    """Reference-table extraction in the Rust core (index-engine-lib, #91).

    Returns the ReferenceTable JSON, or None when the backend is unavailable or
    the core reports an error envelope.
    """
    try:
        backend = _load_backend()
        build_fn = getattr(backend, "build_reference_table_json", None)
        if not callable(build_fn):
            return None
        result = build_fn(files_json)
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "error" in parsed:
            logger.debug("Rust build_reference_table error: %s", parsed["error"])
            return None
        return result
    except Exception as exc:  # noqa: BLE001 - engine boundary must degrade, not crash.
        logger.debug("Rust build_reference_table unavailable: %s", exc, exc_info=True)
        return None


def try_rust_diff_symbol_tables(
    old_json: str, new_json: str
) -> list[dict[str, Any]] | None:
    """Cross-file diff in the Rust core (index-engine-lib, #91).

    *old_json* / *new_json* are serialised symbol tables. Returns the parsed
    list of cross-file change dicts (MOVE_TO_MODULE / SPLIT_MODULE /
    CROSS_FILE_RENAME), or None when the backend is unavailable or the core
    reports an error envelope.
    """
    try:
        backend = _load_backend()
        diff_fn = getattr(backend, "diff_symbol_tables_json", None)
        if not callable(diff_fn):
            return None
        result = json.loads(diff_fn(old_json, new_json))
        if isinstance(result, dict) and "error" in result:
            logger.debug("Rust diff_symbol_tables error: %s", result["error"])
            return None
        return result
    except Exception as exc:  # noqa: BLE001 - engine boundary must degrade, not crash.
        logger.debug("Rust diff_symbol_tables unavailable: %s", exc, exc_info=True)
        return None


def try_rust_evaluate_guardrail_rules(
    request: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Guardrail rule evaluation in the Rust core (#91 A1.3b).

    *request* carries the diff language/filenames, the old/new trees, the diff's
    changed-node ids, and the parsed policy rules. The core matches protected paths
    against the changes and returns the list of violation dicts. Returns None when
    the backend/entrypoint is unavailable or reports an error envelope.
    """
    try:
        backend = _load_backend()
        eval_fn = getattr(backend, "evaluate_guardrail_rules_json", None)
        if not callable(eval_fn):
            return None
        result = json.loads(eval_fn(json.dumps(request)))
        if isinstance(result, dict) and "error" in result:
            logger.debug("Rust evaluate_guardrail_rules error: %s", result["error"])
            return None
        return result
    except Exception as exc:  # noqa: BLE001 - engine boundary must degrade, not crash.
        logger.debug("Rust guardrail evaluation unavailable: %s", exc, exc_info=True)
        return None


def load_project_diff_config_data(
    start_path: str | None, explicit_path: str | None
) -> dict[str, Any]:
    """Resolve the ``intentdiff.yaml`` ``config`` section in the Rust core (#99, A2.1).

    Returns the validated config mapping (empty when there is no file/section).
    Rust-authoritative: the file walk, YAML parse, and unsupported-key rejection run
    in the core. A ``ValueError`` raised by the core (malformed file / unsupported
    keys) propagates unchanged, matching the retired Python behaviour.
    """
    backend = _load_backend()
    load_fn = getattr(backend, "load_project_diff_config_json", None)
    if not callable(load_fn):
        raise RuntimeError("backend does not expose load_project_diff_config_json")
    return json.loads(load_fn(start_path, explicit_path))


def resolve_intentdiff_config_path(
    start_path: str | None, explicit_path: str | None
) -> str | None:
    """Find the nearest ``intentdiff.yaml`` in the Rust core (#99, A2.1)."""
    backend = _load_backend()
    find_fn = getattr(backend, "find_intentdiff_config_path", None)
    if not callable(find_fn):
        raise RuntimeError("backend does not expose find_intentdiff_config_path")
    return find_fn(start_path, explicit_path)


# ── Cache key generation (Rust-authoritative, #101, A2.2) ──────────────────
# Deterministic SHA-256 cache keys computed in the core so every binding agrees.


def cache_make_key(parts: list[str]) -> str:
    """SHA-256 hex of length-prefixed parts (Rust ``store._make_key``)."""
    return _load_backend().cache_make_key(list(parts))


def cache_parse_key(filtered_cst_or_content: str, grammar_id: str, wasm_hash: str) -> str:
    """Cache key for a single-file parser result (Rust)."""
    return _load_backend().cache_parse_key(filtered_cst_or_content, grammar_id, wasm_hash)


def cache_diff_key(
    old_preprocessed: str, new_preprocessed: str, grammar_id: str, wasm_hash: str
) -> str:
    """Cache key for a full-pipeline SemanticDiff result (Rust)."""
    return _load_backend().cache_diff_key(
        old_preprocessed, new_preprocessed, grammar_id, wasm_hash
    )


def cache_hover_map_key(content: str, language: str) -> str:
    """Cache key for a file's LSP hover-type map (Rust)."""
    return _load_backend().cache_hover_map_key(content, language)


class _RustCacheStore:
    """Stateless proxy over the disk-cache C ABI (`cache_*` handlers, #B.5).

    Holds only ``(path, ttl_days, max_mb)`` — no live object crosses the boundary. Each method
    calls the corresponding `cache_*` handler, which resolves a warm ``SqliteStore`` from the
    Rust-side registry keyed on that tuple, so the connection is reused across calls even though
    the API is stateless. Return shapes match the retired PyO3 ``SqliteCacheStore`` pyclass
    exactly, so the Python ``cache.sqlite_store.SqliteCacheStore`` delegator is unchanged: the
    admin methods return JSON *strings* (the delegator ``json.loads`` them); ``get_*`` return the
    stored string or ``None``.
    """

    __slots__ = ("_p", "_t", "_m")

    def __init__(self, path: str, ttl_days: int, max_mb: int) -> None:
        self._p, self._t, self._m = str(path), int(ttl_days), int(max_mb)
        # Open-on-construct parity with the retired pyclass: creates the DB file + schema and
        # runs eviction eagerly, so constructing a store persists an (empty) cache.
        _c_abi_call("cache_open", self._p, self._t, self._m)

    def get_parse(self, key: str) -> str | None:
        return _c_abi_call("cache_get_parse", self._p, self._t, self._m, key)

    def put_parse(self, key: str, value: str, grammar_id: str = "") -> None:
        _c_abi_call("cache_put_parse", self._p, self._t, self._m, key, value, grammar_id)

    def get_diff(self, key: str) -> str | None:
        return _c_abi_call("cache_get_diff", self._p, self._t, self._m, key)

    def put_diff(
        self, key: str, value: str, language: str, old_filename: str, new_filename: str
    ) -> None:
        _c_abi_call(
            "cache_put_diff", self._p, self._t, self._m, key, value, language, old_filename, new_filename
        )

    def get_symbol_index(self, cache_key: str) -> tuple[str, str] | None:
        result = _c_abi_call("cache_get_symbol_index", self._p, self._t, self._m, cache_key)
        return tuple(result) if result is not None else None

    def put_symbol_index(
        self, cache_key: str, symbols_json: str, refs_json: str, file_count: int
    ) -> None:
        _c_abi_call(
            "cache_put_symbol_index", self._p, self._t, self._m, cache_key, symbols_json, refs_json, file_count
        )

    def get_hover_map(self, key: str) -> str | None:
        return _c_abi_call("cache_get_hover_map", self._p, self._t, self._m, key)

    def put_hover_map(self, key: str, value_json: str) -> None:
        _c_abi_call("cache_put_hover_map", self._p, self._t, self._m, key, value_json)

    def stats(self) -> str:
        return json.dumps(_c_abi_call("cache_stats", self._p, self._t, self._m))

    def metrics(self) -> str:
        return json.dumps(_c_abi_call("cache_metrics", self._p, self._t, self._m))

    def list_entries(
        self,
        table: str,
        language: str | None,
        since: int | None,
        before: int | None,
        min_size: int | None,
        max_size: int | None,
        limit: int,
        with_glob: bool,
    ) -> str:
        return json.dumps(
            _c_abi_call(
                "cache_list_entries",
                self._p, self._t, self._m,
                table, language, since, before, min_size, max_size, limit, with_glob,
            )
        )

    def get_entry_metadata(self, key: str, table: str) -> str | None:
        result = _c_abi_call("cache_get_entry_metadata", self._p, self._t, self._m, key, table)
        return json.dumps(result) if result is not None else None

    def get_entry_payload(self, key: str, table: str) -> str | None:
        return _c_abi_call("cache_get_entry_payload", self._p, self._t, self._m, key, table)

    def export_entries(self, table: str) -> str:
        return json.dumps(_c_abi_call("cache_export_entries", self._p, self._t, self._m, table))

    def clear(self, parse: bool, diff: bool, index: bool, hover: bool) -> None:
        _c_abi_call("cache_clear", self._p, self._t, self._m, parse, diff, index, hover)

    def close(self) -> None:
        _c_abi_call("cache_close", self._p, self._t, self._m)


def sqlite_cache_store(path: str, ttl_days: int, max_mb: int) -> Any:
    """Return a stateless proxy over the native SQLite cache store (#101, A2.2; #B.5).

    Rust-internal caching: the store logic + persistent connection live in the core (a warm
    `SqliteStore` in the process-global registry, keyed by ``(path, ttl_days, max_mb)``); this
    returns a stateless C-ABI proxy, so no PyO3 pyclass object crosses the boundary.
    """
    return _RustCacheStore(path, ttl_days, max_mb)


class _RustAnalyticsStore:
    """Stateless proxy over the analytics C ABI (`analytics_*` handlers, #B.5).

    Holds only the store ``path``; each method resolves a warm ``AnalyticsStore`` from the
    Rust-side registry (engine auto-selected at open). Return shapes match the retired PyO3
    ``AnalyticsStore`` pyclass exactly, so the Python ``cache.duckdb_store.DuckDBAnalyticsStore``
    delegator is unchanged: the query methods return JSON *strings* (the delegator ``json.loads``
    them), ``backend`` is a plain string, and ``record_diagnostics_run`` returns the run-id string.
    """

    __slots__ = ("_p",)

    def __init__(self, path: str) -> None:
        self._p = str(path)
        # Open-on-construct parity with the retired pyclass (creates the DB file + schema).
        _c_abi_call("analytics_open", self._p)

    @property
    def backend(self) -> str:
        return _c_abi_call("analytics_backend", self._p)

    def record_diff(self, diff_json: str) -> None:
        _c_abi_call("analytics_record_diff", self._p, diff_json)

    def record_diagnostics_run(
        self,
        diffs_json: list[str],
        command: str,
        repo: str,
        argv_json: str,
        run_id: str | None,
    ) -> str:
        return _c_abi_call(
            "analytics_record_diagnostics_run", self._p, diffs_json, command, repo, argv_json, run_id
        )

    def query(self, sql: str) -> str:
        return json.dumps(_c_abi_call("analytics_query", self._p, sql))

    def query_readonly(self, sql: str) -> str:
        return json.dumps(_c_abi_call("analytics_query_readonly", self._p, sql))

    def most_changed_files(self, limit: int) -> str:
        return json.dumps(_c_abi_call("analytics_most_changed_files", self._p, limit))

    def changes_by_language(self) -> str:
        return json.dumps(_c_abi_call("analytics_changes_by_language", self._p))

    def recent_diagnostic_runs(self, limit: int) -> str:
        return json.dumps(_c_abi_call("analytics_recent_diagnostic_runs", self._p, limit))

    def fuel_by_language(self, limit: int) -> str:
        return json.dumps(_c_abi_call("analytics_fuel_by_language", self._p, limit))

    def top_fuel_hotspots(self, limit: int) -> str:
        return json.dumps(_c_abi_call("analytics_top_fuel_hotspots", self._p, limit))

    def close(self) -> None:
        _c_abi_call("analytics_close", self._p)


def analytics_store(path: str) -> Any:
    """Return a stateless proxy over the native analytics store (#101, A2.2; #B.5).

    Rust-internal: the store logic + connection live in the core (a warm ``AnalyticsStore`` in
    the process-global registry, keyed by path; engine — provided-DuckDB vs bundled-SQLite —
    auto-selected at open), so no PyO3 pyclass object crosses the boundary.
    """
    return _RustAnalyticsStore(path)


# ── Registry-client security validators (Rust-authoritative, #97, A2.3) ────


def validate_registry_ref(git_ref: str, strict: bool = False) -> None:
    """Validate a registry Git ref in the Rust core (path-traversal guard, #88/#97).

    Raises ``ValueError`` when the ref is unsafe for URL-path interpolation (or, in
    ``strict`` mode, is not a full 40-char commit SHA).
    """
    backend = _load_backend()
    fn = getattr(backend, "validate_registry_ref", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose validate_registry_ref")
    fn(git_ref, strict)


def validate_dep_hashes(
    dep_hashes: list[tuple[str, str]],
    allowed_dependencies: list[str],
    package_name: str,
    install_target: str | None,
) -> list[str]:
    """Validate a plugin's ``dep_hashes`` (format + coverage) in the Rust core (#88/#97).

    Returns human-readable error strings ([] = well-formed, hash-pinned install).
    """
    backend = _load_backend()
    fn = getattr(backend, "validate_dep_hashes", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose validate_dep_hashes")
    return fn(dep_hashes, allowed_dependencies, package_name, install_target)


# ── Git source-collection read ops (Rust-authoritative, #98, A2.4) ──────────


def git_source_content(
    repo_path: str, file_path: str, old_ref: str, new_ref: str
) -> tuple[str, str]:
    """Read ``(old_content, new_content)`` of *file_path* at *old_ref*/*new_ref*.

    Delegates the git object-model work (resolve ref, read blob) to the Rust core
    (shelling to the git CLI). Raises ``FileNotFoundError`` when the path is absent
    on either side, ``ValueError`` for an unresolvable ref.
    """
    backend = _load_backend()
    fn = getattr(backend, "git_source_content_json", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose git_source_content_json")
    data = json.loads(fn(repo_path, file_path, old_ref, new_ref))
    return data["old_content"], data["new_content"]


def working_tree_source_content(
    repo_path: str, file_path: str, ref: str
) -> tuple[str, str]:
    """Read ``(old_content, new_content)`` where old = *file_path* blob at *ref* and
    new = the working-tree file on disk. Raises ``FileNotFoundError`` for a missing
    blob or working-tree file, ``ValueError`` for a bare repo / unresolvable ref.
    """
    backend = _load_backend()
    fn = getattr(backend, "working_tree_source_content_json", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose working_tree_source_content_json")
    data = json.loads(fn(repo_path, file_path, ref))
    return data["old_content"], data["new_content"]


def git_repo_toplevel(repo_path: str) -> str:
    """Return the working-tree root for *repo_path*, raising ``ValueError`` when it is
    not inside a git work tree (replaces GitPython's InvalidGitRepositoryError)."""
    backend = _load_backend()
    fn = getattr(backend, "git_repo_toplevel", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose git_repo_toplevel")
    return fn(repo_path)


def changed_commit_sources(
    repo_path: str, old_ref: str, new_ref: str
) -> list[tuple[str, str, str, str, str | None]]:
    """Yield changed-source rows for a commit-to-commit diff via the Rust core.

    Each row is ``(old_content, new_content, old_path, new_path, staging_status)`` with
    ``staging_status`` always ``None`` for commit-to-commit. Binary files are skipped.
    """
    backend = _load_backend()
    fn = getattr(backend, "changed_commit_sources_json", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose changed_commit_sources_json")
    return [tuple(row) for row in json.loads(fn(repo_path, old_ref, new_ref))]


def changed_sources(
    repo_path: str, old_ref: str, new_ref: str
) -> list[tuple[str, str, str, str, str | None]]:
    """Yield changed-source rows for any diff mode via the Rust core (the full
    ``iter_changed_sources`` dispatcher).

    Each row is ``(old_content, new_content, old_path, new_path, staging_status)``.
    ``new_ref`` sentinels: ``""`` = working tree (per-file "staged"/"unstaged" +
    "untracked"), ``":staged"`` = the index, ``":unpushed"`` = commits not yet pushed;
    anything else is a commit-to-commit diff (``staging_status`` is ``None``).
    """
    backend = _load_backend()
    fn = getattr(backend, "changed_sources_json", None)
    if not callable(fn):
        raise RuntimeError("backend does not expose changed_sources_json")
    return [tuple(row) for row in json.loads(fn(repo_path, old_ref, new_ref))]


# ── VCS backend ops (Rust-authoritative, #98, A2.4.4) ───────────────────────
# `vcs` is one of "git" / "hg" / "svn" / "p4"; the core shells to that CLI.


def vcs_backend_resolve_root(vcs: str, repo_path: str) -> str:
    """Work-tree root for *repo_path* under *vcs* (raises ValueError on failure)."""
    return _load_backend().vcs_backend_resolve_root(vcs, repo_path)


def vcs_backend_get_blob(
    vcs: str, repo_path: str, path: str, ref: str, svn_repo_url: str | None = None
) -> str:
    """Content of *path* at *ref* ("" when absent).

    *svn_repo_url* (svn only) targets a repository URL instead of the working copy.
    """
    return _load_backend().vcs_backend_get_blob(vcs, repo_path, path, ref, svn_repo_url)


def vcs_backend_changed_files(
    vcs: str, repo_path: str, ref_a: str, ref_b: str, svn_repo_url: str | None = None
) -> list[dict[str, Any]]:
    """Changed files between two refs as dicts (old_path/new_path/change_type/is_binary).

    *svn_repo_url* (svn only) targets a repository URL instead of the working copy.
    """
    return json.loads(
        _load_backend().vcs_backend_changed_files_json(
            vcs, repo_path, ref_a, ref_b, svn_repo_url
        )
    )


def vcs_backend_working_tree_changes(
    vcs: str, repo_path: str, ref: str
) -> list[dict[str, Any]]:
    """Uncommitted working-tree changes vs *ref* (binaries skipped)."""
    return json.loads(
        _load_backend().vcs_backend_working_tree_changes_json(vcs, repo_path, ref)
    )


def vcs_backend_merge_base(vcs: str, repo_path: str, ref_a: str, ref_b: str) -> str:
    """Common ancestor of two refs (raises ValueError when none)."""
    return _load_backend().vcs_backend_merge_base(vcs, repo_path, ref_a, ref_b)


# --- Live-server protocol layer (#100, A2.5) -------------------------------


def live_error_response(
    seq: int, code: str, message: str, op: str = "error"
) -> dict[str, Any]:
    """The protocol-v2 error envelope (`LiveServer._error_response`)."""
    return json.loads(_load_backend().live_error_response_json(seq, code, message, op))


def live_limits() -> dict[str, Any]:
    """The wire limits advertised in ready/hello (`LiveServer._limits`)."""
    return json.loads(_load_backend().live_limits_json())


def live_capabilities() -> dict[str, Any]:
    """The capability set advertised in ready/hello (`LiveServer._capabilities`)."""
    return json.loads(_load_backend().live_capabilities_json())


def live_normalise_request_path(path: str) -> dict[str, Any]:
    """Repo-relative path guard: `{ok, path}` or `{ok: False, code, message}`."""
    return json.loads(_load_backend().live_normalise_request_path_json(path))


def live_request_seq(request: dict[str, Any]) -> dict[str, Any]:
    """Validate the `seq` field: `{seq}` or `{error}` (`LiveServer._request_seq`)."""
    return json.loads(_load_backend().live_request_seq_json(json.dumps(request)))


def live_parse_diff_request(request: dict[str, Any], default_ref: str) -> dict[str, Any]:
    """Validate a diff request: `{parsed}` or `{error}` (`LiveServer._parse_diff_request`)."""
    return json.loads(
        _load_backend().live_parse_diff_request_json(json.dumps(request), default_ref)
    )


def live_parse_review_request(
    request: dict[str, Any], default_ref: str, seq: int
) -> dict[str, Any]:
    """Validate a review request's refs/stream: `{parsed}` or `{error}`."""
    return json.loads(
        _load_backend().live_parse_review_request_json(
            json.dumps(request), default_ref, seq
        )
    )


def live_handle_diff(
    repo_path: str,
    path: str,
    ref: str,
    content: str,
    config_json: str,
    wasm_dir: str = "",
) -> dict[str, Any]:
    """Native single-file diff (Phase A1/R): `{diff}` (certified) or `{fallback: reason}`.

    Serves any file the bundled-parser manifest in *wasm_dir* resolves (Python via the native
    tree-sitter path, other languages via their wasm parser); unresolved extensions and
    non-certified outcomes return a fallback marker for the Python differ path.
    """
    return json.loads(
        _load_backend().live_handle_diff_json(
            repo_path, path, ref, content, config_json, wasm_dir
        )
    )


def live_handle_review(
    repo_path: str, old_ref: str, new_ref: str, config_json: str, wasm_dir: str = ""
) -> dict[str, Any]:
    """Native commit review (Phase A2 + d2-review): `{commit_diff}` or `{fallback: reason}`.

    Each changed file resolves via the bundled-parser manifest in *wasm_dir*: an all-Python commit
    runs the certified batch commit; a commit with resolvable non-Python files is served per-file
    (Python via the certified batch item, other languages via the native single-file chain).
    Unresolvable extensions and non-served outcomes return a fallback marker for the Python
    CommitDiffer path. Older cores without the wasm_dir parameter degrade to the Python-only form.
    """
    backend = _load_backend()
    try:
        raw = backend.live_handle_review_json(repo_path, old_ref, new_ref, config_json, wasm_dir)
    except TypeError:
        # Older core: no wasm_dir parameter -> Python-only certified commit (non-Python falls back).
        raw = backend.live_handle_review_json(repo_path, old_ref, new_ref, config_json)
    return json.loads(raw)


_registered_xml_dialects_fingerprint: str | None = None


def try_register_user_xml_dialects(dialects: list[dict[str, Any]]) -> bool:
    """Marshal user XML dialect specs (issue #86) into the Rust registry.

    Idempotent per payload; returns False when the backend or the entrypoint is
    unavailable (older core), in which case the diff proceeds without user XML
    dialects — the registration is additive, never load-bearing.
    """
    global _registered_xml_dialects_fingerprint
    payload = json.dumps(dialects, sort_keys=True)
    if payload == _registered_xml_dialects_fingerprint:
        return True
    try:
        backend = _load_backend()
        register_fn = getattr(backend, "register_user_xml_dialects_json", None)
        if not callable(register_fn):
            return False
        register_fn(payload)
        _registered_xml_dialects_fingerprint = payload
        return True
    except Exception as exc:  # noqa: BLE001 - strangler boundary must fall back.
        logger.debug("Rust XML dialect registration unavailable: %s", exc, exc_info=True)
        return False


def _run_invariance_request(request: dict[str, Any]) -> RustInvarianceResult:
    try:
        backend = _load_backend()
        apply_fn = getattr(backend, "apply_invariances_json", None)
        if not callable(apply_fn):
            raise RuntimeError("backend does not expose apply_invariances_json")
        payload = apply_fn(json.dumps(request))
        data = json.loads(payload)
    except Exception as exc:  # noqa: BLE001 - public engine boundary requires Rust here.
        raise RuntimeError(f"Rust invariance engine unavailable: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Rust invariance engine returned a non-object payload")
    return RustInvarianceResult(
        changes=[Change.model_validate(item) for item in data.get("changes") or []],
        change_groups=[
            ChangeGroup.model_validate(item) for item in data.get("change_groups") or []
        ],
        ignored_style_changes=[
            dict(item)
            for item in data.get("ignored_style_changes") or []
            if isinstance(item, dict)
        ],
    )


def try_rust_core_batch_diff(
    *,
    old_source: str,
    new_source: str,
    old_filename: str,
    new_filename: str,
    parser_wasm_path: str,
    language: str,
    parser_plugin_id: str | None,
    config: DiffConfig,
    file_lifecycle: str | None = None,
) -> RustCoreBatchAttempt:
    """Try the planned Rust V4 batch boundary and fall back unless final output is ready."""
    if not rust_core_enabled(config):
        return RustCoreBatchAttempt(fallback_reason="disabled")
    if language != "python":
        return RustCoreBatchAttempt(fallback_reason="unsupported language")
    parser_plugin_id = _canonical_python_plugin_id(parser_plugin_id)
    if parser_plugin_id is not None and parser_plugin_id not in _PYTHON_PLUGIN_IDS:
        return RustCoreBatchAttempt(fallback_reason="unsupported parser plugin")
    if config.diagnostics:
        return RustCoreBatchAttempt(fallback_reason="diagnostics require Python pipeline")

    batch = try_rust_core_batch_diffs(
        files=[
            {
                "old_source": old_source,
                "new_source": new_source,
                "old_filename": old_filename,
                "new_filename": new_filename,
                "language": language,
                "parser_plugin_id": parser_plugin_id or "",
                "parser_wasm_path": parser_wasm_path,
                "file_lifecycle": file_lifecycle or "",
            }
        ],
        config=config,
    )
    if batch.diffs:
        return RustCoreBatchAttempt(
            diff=batch.diffs[0],
            backend_version=batch.backend_version,
        )
    return RustCoreBatchAttempt(
        fallback_reason=batch.fallback_reasons.get(0)
        or batch.fallback_reason
        or "batch item is incomplete",
        backend_version=batch.backend_version,
    )


def _normalize_builtin_change_type_enums(diff: SemanticDiff) -> SemanticDiff:
    """Normalize known Rust JSON change types to public ChangeType enums."""

    normalized_changes: list[Change] = []
    changed = False
    for change in diff.changes:
        change_type = change.change_type
        if isinstance(change_type, str):
            try:
                enum_change_type = ChangeType(change_type)
            except ValueError:
                normalized_changes.append(change)
                continue
            normalized_changes.append(
                change.model_copy(update={"change_type": enum_change_type})
            )
            changed = True
        else:
            normalized_changes.append(change)
    if not changed:
        return diff
    return diff.model_copy(update={"changes": normalized_changes})


def _stamp_certified_rust_diff(metadata: dict[str, Any]) -> dict[str, Any]:
    """Stamp the public ownership contract for accepted Rust-finalized diffs."""

    stamped = dict(metadata)
    stamped.setdefault("engine_owner", "rust")
    stamped.setdefault("semantic_contract", "rust_finalized_v1")
    return stamped


def _copy_diff_with_metadata(diff: SemanticDiff, metadata: dict[str, Any]) -> SemanticDiff:
    payload = diff.model_dump(mode="json")
    payload["metadata"] = metadata
    return SemanticDiff.model_validate(payload)


def try_rust_core_batch_diffs(
    *,
    files: list[dict[str, Any]],
    config: DiffConfig,
    parallel: bool = False,
    max_workers: int | None = None,
    python_parser_backend: str = "native",
) -> RustCoreBatchDiffsAttempt:
    """Try the Rust V4 batch boundary for multiple certified Python file pairs."""
    if not rust_core_enabled(config):
        return RustCoreBatchDiffsAttempt(fallback_reason="disabled")
    if config.diagnostics:
        return RustCoreBatchDiffsAttempt(
            fallback_reason="diagnostics require Python pipeline"
        )
    if not files:
        return RustCoreBatchDiffsAttempt()

    adapter_phase_timings: list[dict[str, Any]] = []

    def _record_adapter_phase(
        name: str, started: float, *, summary_exclude: bool = False
    ) -> None:
        phase = {
            "name": name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "shared": True,
        }
        if summary_exclude:
            phase["summary_exclude"] = True
        adapter_phase_timings.append(phase)

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must never break diffing.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreBatchDiffsAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        supports = getattr(backend, "supports_language", None)
        if callable(supports) and not supports("python"):
            return RustCoreBatchDiffsAttempt(
                fallback_reason="backend does not support python"
            )
        backend_version = str(getattr(backend, "version", lambda: "")())
        diff_batch = getattr(backend, "diff_batch", None)
        if not callable(diff_batch):
            return RustCoreBatchDiffsAttempt(
                fallback_reason="backend does not expose diff_batch",
                backend_version=backend_version,
            )
        phase_started = time.perf_counter()
        normalized_files = _canonicalize_file_plugin_ids(files)
        request = {
            "schema_version": 1,
            "mode": "commit_batch",
            "python_parser_backend": python_parser_backend,
            "config": config.model_dump(mode="json"),
            "parallel": parallel,
            "max_workers": max_workers,
            "files": normalized_files,
        }
        _record_adapter_phase("rust_core_request_construction", phase_started)
        phase_started = time.perf_counter()
        request_json = json.dumps(request)
        _record_adapter_phase("rust_core_request_json_encode", phase_started)
        phase_started = time.perf_counter()
        raw_payload = diff_batch(request_json)
        _record_adapter_phase("rust_core_batch_execution", phase_started)
        phase_started = time.perf_counter()
        data = _load_tree_payload(raw_payload)
        _record_adapter_phase("rust_core_response_json_decode", phase_started)
        phase_started = time.perf_counter()
        engine = str(data.get("engine") or "")
        if engine != _BATCH_ENGINE:
            _record_adapter_phase("rust_core_response_validation", phase_started)
            return RustCoreBatchDiffsAttempt(
                fallback_reason=f"unexpected engine: {engine or '<missing>'}",
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
            )
        diff_items = data.get("diffs")
        if not isinstance(diff_items, list):
            reason = str(data.get("reason") or "batch response did not include a diff")
            _record_adapter_phase("rust_core_response_validation", phase_started)
            return RustCoreBatchDiffsAttempt(
                fallback_reason=reason,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
            )
        batch_metadata = dict(data.get("metadata") or {})
        fallback_reasons: dict[int, str] = {}
        validated_diffs: list[tuple[int, str, SemanticDiff]] = []
        for fallback_index, item in enumerate(diff_items):
            if not isinstance(item, dict):
                fallback_reasons[fallback_index] = "batch diff item must be an object"
                continue
            index = int(item.get("index") or fallback_index)
            status = str(item.get("status") or "")
            if status != _COMPLETE_STATUS or "diff" not in item:
                fallback_reasons[index] = str(
                    item.get("reason") or status or "batch item is incomplete"
                )
                continue
            diff = _normalize_builtin_change_type_enums(
                SemanticDiff.model_validate(item["diff"])
            )
            validated_diffs.append((index, status, diff))
        _record_adapter_phase("rust_core_response_validation", phase_started)
        phase_started = time.perf_counter()
        diffs: dict[int, SemanticDiff] = {}
        for index, status, diff in validated_diffs:
            metadata = dict(diff.metadata)
            rust_metadata = dict(metadata.get("rust_core") or {})
            rust_metadata.update(
                {
                    "status": status,
                    "used": True,
                    "backend_version": backend_version,
                    "engine": _BATCH_ENGINE,
                    "stage": "batch_final_diff",
                    "boundary": "source_batch_to_final_diff",
                    "cache_policy": "python_diff_cache_skipped",
                }
            )
            metadata["rust_core"] = rust_metadata
            if batch_metadata:
                metadata["rust_core_batch"] = batch_metadata
            diffs[index] = _copy_diff_with_metadata(
                diff,
                _stamp_certified_rust_diff(metadata),
            )
        _record_adapter_phase("rust_core_result_assembly", phase_started)
        if adapter_phase_timings:
            batch_metadata = {
                **batch_metadata,
                "adapter_phase_timings": adapter_phase_timings,
            }
            for index, diff in list(diffs.items()):
                metadata = dict(diff.metadata)
                metadata["rust_core_batch"] = batch_metadata
                diffs[index] = _copy_diff_with_metadata(
                    diff,
                    _stamp_certified_rust_diff(metadata),
                )
        return RustCoreBatchDiffsAttempt(
            diffs=diffs,
            fallback_reasons=fallback_reasons,
            batch_metadata=batch_metadata,
            adapter_phase_timings=adapter_phase_timings,
            backend_version=backend_version,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core batch output rejected: %s", exc)
        return RustCoreBatchDiffsAttempt(
            fallback_reason=f"invalid output: {exc}",
            adapter_phase_timings=adapter_phase_timings,
        )
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core batch path failed: %s", exc, exc_info=True)
        return RustCoreBatchDiffsAttempt(
            fallback_reason=f"failed: {exc}",
            adapter_phase_timings=adapter_phase_timings,
        )


def try_rust_core_commit_json(
    *,
    files: list[dict[str, Any]],
    old_ref: str,
    new_ref: str,
    config: DiffConfig,
    parallel: bool = False,
    max_workers: int | None = None,
    python_parser_backend: str = "native",
) -> RustCoreCommitJsonAttempt:
    """Return certified Rust CommitDiff JSON bytes without Pydantic hot-path validation."""
    if not rust_core_enabled(config):
        return RustCoreCommitJsonAttempt(fallback_reason="disabled")
    if config.diagnostics:
        return RustCoreCommitJsonAttempt(
            fallback_reason="diagnostics require Python pipeline"
        )
    if python_parser_backend != "native":
        return RustCoreCommitJsonAttempt(
            fallback_reason="certified commit JSON requires native Python backend"
        )
    if not files:
        return RustCoreCommitJsonAttempt()

    adapter_phase_timings: list[dict[str, Any]] = []

    def _record_adapter_phase(
        name: str, started: float, *, summary_exclude: bool = False
    ) -> None:
        phase = {
            "name": name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "shared": True,
        }
        if summary_exclude:
            phase["summary_exclude"] = True
        adapter_phase_timings.append(phase)

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must never break diffing.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreCommitJsonAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        supports = getattr(backend, "supports_language", None)
        if callable(supports) and not supports("python"):
            return RustCoreCommitJsonAttempt(
                fallback_reason="backend does not support python"
            )
        backend_version = str(getattr(backend, "version", lambda: "")())
        diff_batch_commit_json = getattr(backend, "diff_batch_commit_json", None)
        if not callable(diff_batch_commit_json):
            return RustCoreCommitJsonAttempt(
                fallback_reason="backend does not expose diff_batch_commit_json",
                backend_version=backend_version,
            )
        phase_started = time.perf_counter()
        normalized_files = _canonicalize_file_plugin_ids(files)
        request = {
            "schema_version": 1,
            "mode": "commit_json",
            "old_ref": old_ref,
            "new_ref": new_ref,
            "python_parser_backend": python_parser_backend,
            "config": config.model_dump(mode="json"),
            "parallel": parallel,
            "max_workers": max_workers,
            "files": normalized_files,
        }
        _record_adapter_phase("rust_core_commit_request_construction", phase_started)
        phase_started = time.perf_counter()
        request_json = json.dumps(request)
        _record_adapter_phase("rust_core_commit_request_json_encode", phase_started)
        phase_started = time.perf_counter()
        control_json, commit_payload = diff_batch_commit_json(request_json)
        _record_adapter_phase(
            "rust_core_commit_pyo3_call",
            phase_started,
            summary_exclude=True,
        )
        phase_started = time.perf_counter()
        control = json.loads(control_json)
        if not isinstance(control, dict):
            raise ValueError("certified commit control must be a JSON object")
        _record_adapter_phase("rust_core_commit_control_decode", phase_started)
        phase_started = time.perf_counter()
        status = str(control.get("status") or "")
        certification = str(control.get("certification") or "")
        if status != _COMPLETE_STATUS:
            reason = str(control.get("reason") or status or "commit JSON incomplete")
            _record_adapter_phase("rust_core_commit_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason=reason,
            )
        if certification != _PYTHON_NATIVE_CERTIFICATION:
            _record_adapter_phase("rust_core_commit_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason=(
                    f"unexpected certification: {certification or '<missing>'}"
                ),
            )
        if commit_payload is None:
            _record_adapter_phase("rust_core_commit_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason="commit JSON bytes missing",
            )
        commit_json = bytes(commit_payload)
        if not commit_json:
            _record_adapter_phase("rust_core_commit_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason="commit JSON bytes empty",
            )
        declared_size = int(control.get("commit_diff_byte_size") or 0)
        if declared_size != len(commit_json):
            _record_adapter_phase("rust_core_commit_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason=(
                    "commit JSON byte size mismatch: "
                    f"control={declared_size}, payload={len(commit_json)}"
                ),
            )
        _record_adapter_phase("rust_core_commit_control_validation", phase_started)
        if os.getenv("INTENTDIFF_RUST_CORE_VALIDATE_JSON", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            phase_started = time.perf_counter()
            from intentdiff.core.models import CommitDiff

            CommitDiff.model_validate_json(commit_json)
            _record_adapter_phase("rust_core_commit_debug_pydantic_validation", phase_started)
        return RustCoreCommitJsonAttempt(
            control=control,
            commit_diff_json=commit_json,
            adapter_phase_timings=adapter_phase_timings,
            backend_version=backend_version,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core certified commit JSON rejected: %s", exc)
        return RustCoreCommitJsonAttempt(
            fallback_reason=f"invalid output: {exc}",
            adapter_phase_timings=adapter_phase_timings,
        )
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core certified commit JSON failed: %s", exc, exc_info=True)
        return RustCoreCommitJsonAttempt(
            fallback_reason=f"failed: {exc}",
            adapter_phase_timings=adapter_phase_timings,
        )


def try_rust_core_working_tree_commit_json(
    *,
    repo_path: str | Path,
    old_ref: str,
    config: DiffConfig,
    parallel: bool = False,
    max_workers: int | None = None,
    python_parser_backend: str = "native",
) -> RustCoreCommitJsonAttempt:
    """Return Rust-certified CommitDiff JSON with Rust-side source collection."""
    if not rust_core_enabled(config):
        return RustCoreCommitJsonAttempt(fallback_reason="disabled")
    if config.diagnostics:
        return RustCoreCommitJsonAttempt(
            fallback_reason="diagnostics require Python pipeline"
        )

    adapter_phase_timings: list[dict[str, Any]] = []

    def _record_adapter_phase(
        name: str, started: float, *, summary_exclude: bool = False
    ) -> None:
        phase = {
            "name": name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "shared": True,
        }
        if summary_exclude:
            phase["summary_exclude"] = True
        adapter_phase_timings.append(phase)

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreCommitJsonAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        backend_version = str(getattr(backend, "version", lambda: "")())
        entrypoint = getattr(backend, "diff_working_tree_python_commit_json", None)
        if not callable(entrypoint):
            return RustCoreCommitJsonAttempt(
                fallback_reason="backend does not expose diff_working_tree_python_commit_json",
                backend_version=backend_version,
            )

        phase_started = time.perf_counter()
        request = {
            "schema_version": 1,
            "mode": "working_tree_commit_json",
            "repo_path": str(repo_path),
            "old_ref": old_ref,
            "new_ref": "",
            "python_parser_backend": python_parser_backend,
            "config": config.model_dump(mode="json"),
            "parallel": parallel,
            "max_workers": max_workers,
        }
        _record_adapter_phase("rust_core_working_tree_request_construction", phase_started)
        phase_started = time.perf_counter()
        request_json = json.dumps(request)
        _record_adapter_phase("rust_core_working_tree_request_json_encode", phase_started)
        phase_started = time.perf_counter()
        control_json, commit_payload = entrypoint(request_json)
        _record_adapter_phase(
            "rust_core_working_tree_pyo3_call",
            phase_started,
            summary_exclude=True,
        )
        phase_started = time.perf_counter()
        control = json.loads(control_json)
        if not isinstance(control, dict):
            raise ValueError("certified working-tree control must be a JSON object")
        _record_adapter_phase("rust_core_working_tree_control_decode", phase_started)

        phase_started = time.perf_counter()
        status = str(control.get("status") or "")
        certification = str(control.get("certification") or "")
        if status != _COMPLETE_STATUS:
            reason = str(control.get("reason") or status or "working-tree JSON incomplete")
            _record_adapter_phase("rust_core_working_tree_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason=reason,
            )
        if certification != _PYTHON_NATIVE_CERTIFICATION:
            _record_adapter_phase("rust_core_working_tree_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason=f"unexpected certification: {certification or '<missing>'}",
            )
        if commit_payload is None:
            _record_adapter_phase("rust_core_working_tree_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason="commit JSON bytes missing",
            )
        commit_json = bytes(commit_payload)
        declared_size = int(control.get("commit_diff_byte_size") or 0)
        if declared_size != len(commit_json):
            _record_adapter_phase("rust_core_working_tree_control_validation", phase_started)
            return RustCoreCommitJsonAttempt(
                control=control,
                adapter_phase_timings=adapter_phase_timings,
                backend_version=backend_version,
                fallback_reason=(
                    "commit JSON byte size mismatch: "
                    f"control={declared_size}, payload={len(commit_json)}"
                ),
            )
        _record_adapter_phase("rust_core_working_tree_control_validation", phase_started)
        return RustCoreCommitJsonAttempt(
            control=control,
            commit_diff_json=commit_json,
            adapter_phase_timings=adapter_phase_timings,
            backend_version=backend_version,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core working-tree commit JSON rejected: %s", exc)
        return RustCoreCommitJsonAttempt(
            fallback_reason=f"invalid output: {exc}",
            adapter_phase_timings=adapter_phase_timings,
        )
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core working-tree commit JSON failed: %s", exc, exc_info=True)
        return RustCoreCommitJsonAttempt(
            fallback_reason=f"failed: {exc}",
            adapter_phase_timings=adapter_phase_timings,
        )


def run_rust_core_candidate_batch(
    *,
    files: list[dict[str, Any]],
    config: DiffConfig,
    python_parser_backend: str = "wasm",
) -> RustCoreCandidateBatchAttempt:
    """Run the internal V4-A candidate lane without making it product-visible."""
    if not rust_core_enabled(config):
        return RustCoreCandidateBatchAttempt(fallback_reason="disabled")

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional benchmark lane records failures.
        logger.debug("Rust core unavailable for candidate batch: %s", exc)
        return RustCoreCandidateBatchAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        backend_version = str(getattr(backend, "version", lambda: "")())
        diff_batch = getattr(backend, "diff_batch", None)
        if not callable(diff_batch):
            return RustCoreCandidateBatchAttempt(
                fallback_reason="backend does not expose diff_batch",
                backend_version=backend_version,
            )
        request = {
            "schema_version": 1,
            "mode": "candidate_batch",
            "candidate": True,
            "python_parser_backend": python_parser_backend,
            "config": config.model_dump(mode="json"),
            "files": _canonicalize_file_plugin_ids(files),
        }
        data = _load_tree_payload(diff_batch(json.dumps(request)))
        engine = str(data.get("engine") or "")
        if engine != _BATCH_ENGINE:
            return RustCoreCandidateBatchAttempt(
                fallback_reason=f"unexpected engine: {engine or '<missing>'}",
                backend_version=backend_version,
            )
        for item in data.get("diffs") or []:
            if not isinstance(item, dict):
                continue
            candidate_diff = item.get("candidate_diff")
            if candidate_diff is not None:
                SemanticDiff.model_validate(candidate_diff)
        return RustCoreCandidateBatchAttempt(
            payload=data if isinstance(data, dict) else None,
            backend_version=backend_version,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core candidate output rejected: %s", exc)
        return RustCoreCandidateBatchAttempt(
            fallback_reason=f"invalid output: {exc}",
            backend_version=locals().get("backend_version", ""),
        )
    except Exception as exc:  # noqa: BLE001 - benchmark lane must report failures.
        logger.debug("Rust core candidate batch failed: %s", exc, exc_info=True)
        return RustCoreCandidateBatchAttempt(
            fallback_reason=f"failed: {exc}",
            backend_version=locals().get("backend_version", ""),
        )


def try_rust_core_cst_diff(
    *,
    old_filtered_cst_json: str,
    new_filtered_cst_json: str,
    old_raw_source: str,
    new_raw_source: str,
    old_filename: str,
    new_filename: str,
    parser_wasm_path: str,
    language: str,
    parser_plugin_id: str | None,
    config: DiffConfig,
) -> RustCoreAttempt:
    """Try the Rust core after Python has produced filtered CST JSON."""
    if not rust_core_enabled(config):
        return RustCoreAttempt(fallback_reason="disabled")
    if language != "python":
        return RustCoreAttempt(fallback_reason="unsupported language")
    parser_plugin_id = _canonical_python_plugin_id(parser_plugin_id)
    if parser_plugin_id is not None and parser_plugin_id not in _PYTHON_PLUGIN_IDS:
        return RustCoreAttempt(fallback_reason="unsupported parser plugin")
    if config.diagnostics:
        return RustCoreAttempt(fallback_reason="diagnostics require Python pipeline")

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must never break diffing.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        supports = getattr(backend, "supports_language", None)
        if callable(supports) and not supports("python"):
            return RustCoreAttempt(fallback_reason="backend does not support python")
        backend_version = str(getattr(backend, "version", lambda: "")())
        diff_fn = getattr(backend, "diff_python_cst_json", None)
        if not callable(diff_fn):
            return RustCoreAttempt(
                fallback_reason="backend does not expose diff_python_cst_json",
                backend_version=backend_version,
            )
        payload = diff_fn(
            old_filtered_cst_json,
            new_filtered_cst_json,
            old_raw_source,
            new_raw_source,
            old_filename,
            new_filename,
            parser_wasm_path,
            config.model_dump_json(),
        )
        diff = SemanticDiff.model_validate_json(payload)
        metadata = dict(diff.metadata)
        rust_metadata = dict(metadata.get("rust_core") or {})
        status = str(rust_metadata.get("status") or "")
        if status != _COMPLETE_STATUS:
            return RustCoreAttempt(
                fallback_reason=f"incomplete result status: {status or '<missing>'}",
                backend_version=backend_version,
            )
        rust_metadata.update(
            {
                "used": True,
                "backend_version": backend_version,
            }
        )
        metadata["rust_core"] = rust_metadata
        diff = diff.model_copy(update={"metadata": metadata})
        return RustCoreAttempt(diff=diff, backend_version=backend_version)
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core CST output rejected: %s", exc)
        return RustCoreAttempt(fallback_reason=f"invalid output: {exc}")
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core CST path failed: %s", exc, exc_info=True)
        return RustCoreAttempt(fallback_reason=f"failed: {exc}")


def try_rust_core_tree_diff(
    *,
    old_tree: SemanticNode,
    new_tree: SemanticNode,
    old_filename: str,
    new_filename: str,
    language: str,
    config: DiffConfig,
) -> RustCoreTreeAttempt:
    """Try Rust stage 9-11 work after Python has built validated semantic trees.

    Since migration step 2 (see ``docs/ENGINE_BOUNDARY_AUDIT.md``) the Rust
    matching/edit-script pipeline is language-agnostic — it operates on the
    ``SemanticNode`` shape, not on Python-specific AST facts. Any language
    label is accepted; the Python-only guard was a certification gate, not
    an algorithm limitation.
    """
    if not rust_core_enabled(config):
        return RustCoreTreeAttempt(fallback_reason="disabled")
    if config.diagnostics:
        return RustCoreTreeAttempt(fallback_reason="diagnostics require Python pipeline")

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must never break diffing.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreTreeAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        backend_version = str(getattr(backend, "version", lambda: "")())
        # Prefer the language-agnostic entrypoint added in migration step 2.
        # Fall back to the legacy Python-only entrypoint for older installs.
        diff_fn = getattr(backend, "diff_semantic_tree_json", None) or getattr(
            backend, "diff_python_semantic_tree_json", None
        )
        if not callable(diff_fn):
            return RustCoreTreeAttempt(
                fallback_reason="backend exposes no semantic-tree diff entrypoint",
                backend_version=backend_version,
            )
        # Only the legacy Python-only entrypoint requires the language guard.
        if (
            diff_fn.__name__ == "diff_python_semantic_tree_json"
            and language != "python"
        ):
            return RustCoreTreeAttempt(
                fallback_reason=(
                    "installed backend only exposes the Python-only entrypoint; "
                    "rebuild the Rust core to diff non-Python languages"
                ),
                backend_version=backend_version,
            )
        payload = diff_fn(
            old_tree.model_dump_json(),
            new_tree.model_dump_json(),
            old_filename,
            new_filename,
            language,
            config.model_dump_json(),
        )
        data = _load_tree_payload(payload)
        status = str(data.get("status") or "")
        if status != _COMPLETE_STATUS:
            return RustCoreTreeAttempt(
                fallback_reason=f"incomplete result status: {status or '<missing>'}",
                backend_version=backend_version,
            )
        engine = str(data.get("engine") or "")
        if engine not in {_TREE_ENGINE, _TREE_ENGINE_V3}:
            return RustCoreTreeAttempt(
                fallback_reason=f"unexpected engine: {engine or '<missing>'}",
                backend_version=backend_version,
            )
        old_lookup = _semantic_node_lookup(old_tree)
        new_lookup = _semantic_node_lookup(new_tree)
        matching = _matching_from_payload(data.get("matching_pairs"), old_lookup, new_lookup)
        changes = _changes_from_payload(data.get("changes"), old_lookup, new_lookup)
        metadata = dict(data.get("metadata") or {})
        metadata["rust_core"] = {
            "status": status,
            "used": True,
            "backend_version": backend_version,
            "engine": engine,
            "stage": "semantic_tree_stage9_11",
        }
        return RustCoreTreeAttempt(
            changes=changes,
            matching=matching,
            metadata=metadata,
            backend_version=backend_version,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core semantic-tree output rejected: %s", exc)
        return RustCoreTreeAttempt(fallback_reason=f"invalid output: {exc}")
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core semantic-tree path failed: %s", exc, exc_info=True)
        return RustCoreTreeAttempt(fallback_reason=f"failed: {exc}")


def try_rust_core_sources_stage11(
    *,
    old_source: str,
    new_source: str,
    old_filename: str,
    new_filename: str,
    parser_wasm_path: str,
    language: str,
    parser_plugin_id: str | None,
    config: DiffConfig,
) -> RustCoreSourceAttempt:
    """Try Rust V3 from raw Python source through initial Stage 11 output."""
    if not rust_core_enabled(config):
        return RustCoreSourceAttempt(fallback_reason="disabled")
    if language != "python":
        return RustCoreSourceAttempt(fallback_reason="unsupported language")
    parser_plugin_id = _canonical_python_plugin_id(parser_plugin_id)
    if parser_plugin_id is not None and parser_plugin_id not in _PYTHON_PLUGIN_IDS:
        return RustCoreSourceAttempt(fallback_reason="unsupported parser plugin")
    if config.diagnostics:
        return RustCoreSourceAttempt(fallback_reason="diagnostics require Python pipeline")

    try:
        backend = _load_backend()
    except Exception as exc:  # noqa: BLE001 - optional accelerator must never break diffing.
        logger.debug("Rust core unavailable: %s", exc)
        return RustCoreSourceAttempt(fallback_reason=f"unavailable: {exc}")

    try:
        supports = getattr(backend, "supports_language", None)
        if callable(supports) and not supports("python"):
            return RustCoreSourceAttempt(fallback_reason="backend does not support python")
        backend_version = str(getattr(backend, "version", lambda: "")())
        diff_fn = getattr(backend, "diff_python_sources_stage11_json", None)
        if not callable(diff_fn):
            return RustCoreSourceAttempt(
                fallback_reason="backend does not expose diff_python_sources_stage11_json",
                backend_version=backend_version,
            )
        payload = diff_fn(
            old_source,
            new_source,
            old_filename,
            new_filename,
            parser_wasm_path,
            config.model_dump_json(),
        )
        data = _load_tree_payload(payload)
        status = str(data.get("status") or "")
        if status != _COMPLETE_STATUS:
            reason = str(data.get("reason") or status or "<missing>")
            return RustCoreSourceAttempt(
                fallback_reason=f"incomplete result status: {reason}",
                backend_version=backend_version,
            )
        engine = str(data.get("engine") or "")
        if engine != _SOURCE_ENGINE:
            return RustCoreSourceAttempt(
                fallback_reason=f"unexpected engine: {engine or '<missing>'}",
                backend_version=backend_version,
            )
        old_tree = SemanticNode.model_validate(data.get("old_tree"))
        new_tree = SemanticNode.model_validate(data.get("new_tree"))
        old_lookup = _semantic_node_lookup(old_tree)
        new_lookup = _semantic_node_lookup(new_tree)
        matching = _matching_from_payload(data.get("matching_pairs"), old_lookup, new_lookup)
        changes = _changes_from_payload(data.get("changes"), old_lookup, new_lookup)
        adaptive_fuel = int(data.get("adaptive_fuel") or config.plugin_fuel)
        metadata = dict(data.get("metadata") or {})
        rust_phase_timings = metadata.pop("phase_timings", None)
        if rust_phase_timings is not None:
            metadata["rust_phase_timings"] = rust_phase_timings
        metadata["rust_core"] = {
            "status": status,
            "used": True,
            "backend_version": backend_version,
            "engine": engine,
            "stage": "sources_to_stage11",
            "wasm_boundary": metadata.get("wasm_boundary", "rust_wasmtime"),
        }
        return RustCoreSourceAttempt(
            old_tree=old_tree,
            new_tree=new_tree,
            changes=changes,
            matching=matching,
            adaptive_fuel=adaptive_fuel,
            metadata=metadata,
            backend_version=backend_version,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.debug("Rust core source-stage output rejected: %s", exc)
        return RustCoreSourceAttempt(fallback_reason=f"invalid output: {exc}")
    except Exception as exc:  # noqa: BLE001 - optional accelerator must fall back.
        logger.debug("Rust core source-stage path failed: %s", exc, exc_info=True)
        return RustCoreSourceAttempt(fallback_reason=f"failed: {exc}")


def _load_tree_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        import json

        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, dict):
        raise ValueError("Rust core tree payload must be a JSON object")
    return data


def _semantic_node_lookup(root: SemanticNode) -> dict[str, SemanticNode]:
    nodes = [root, *root.descendants()]
    return {node.id: node for node in nodes}


def _matching_from_payload(
    raw_pairs: Any,
    old_lookup: dict[str, SemanticNode],
    new_lookup: dict[str, SemanticNode],
) -> Matching:
    if not isinstance(raw_pairs, list):
        raise ValueError("matching_pairs must be a list")
    matching: Matching = []
    for raw_pair in raw_pairs:
        if not isinstance(raw_pair, dict):
            raise ValueError("matching pair must be an object")
        old_id = str(raw_pair.get("old_id") or "")
        new_id = str(raw_pair.get("new_id") or "")
        try:
            old_node = old_lookup[old_id]
            new_node = new_lookup[new_id]
        except KeyError as exc:
            raise ValueError(f"matching pair references unknown node id: {exc}") from exc
        matching.append(Match(old_node, new_node))
    return matching


def _changes_from_payload(
    raw_changes: Any,
    old_lookup: dict[str, SemanticNode],
    new_lookup: dict[str, SemanticNode],
) -> list[Change]:
    if not isinstance(raw_changes, list):
        raise ValueError("changes must be a list")
    changes: list[Change] = []
    for raw_change in raw_changes:
        change = Change.model_validate(raw_change)
        updates: dict[str, Any] = {}
        if change.old_node is not None:
            old_node = old_lookup.get(change.old_node.id)
            if old_node is None:
                raise ValueError(f"change references unknown old node id: {change.old_node.id}")
            updates["old_node"] = old_node
        if change.new_node is not None:
            new_node = new_lookup.get(change.new_node.id)
            if new_node is None:
                raise ValueError(f"change references unknown new node id: {change.new_node.id}")
            updates["new_node"] = new_node
        changes.append(change.model_copy(update=updates) if updates else change)
    return changes
