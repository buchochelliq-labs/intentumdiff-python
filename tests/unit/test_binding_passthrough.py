"""Constraint tests: the binding transports core results and adds no decisions (#338).

See docs/THIN_BINDING_AUDIT.md, "Constraint tests". The C-ABI call layer in ``rust_core``
(``_c_abi_lib``) is replaced by a recorder that forwards to the real engine and lets a test
substitute canned envelopes for chosen operations. Then:

(a) passthrough — the public DTO equals the canned core result (documented DTO mapping only);
(b) errors surface — a canned ``{"ok": false}`` raises the mapped exception, and Python never
    calls a second review operation as a substitute;
(c) a static AST check over the non-shell package forbids new language-id set literals and
    ``except Exception`` blocks that return a value. Current offenders are an explicit
    allowlist that may only shrink.

Where Python still rewrites core output, the exact-equality assertion is ``xfail(strict=True)``
with the audit slice that removes the rewrite, so the gap stays visible and the marker must be
dropped the moment the rewrite is gone.
"""

from __future__ import annotations

import ast
import copy
import ctypes
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from intentumdiff import CoreSizeLimitError, SemanticDiffer, review_text
from intentumdiff import rust_core
from intentumdiff.core.models import SemanticDiff

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE = _ROOT / "src" / "intentumdiff"

# Operations that produce a complete review (or a substitute for one). Python must never
# pick one of these as a second attempt after core answered or failed.
_REVIEW_OPS = {
    "review_text",
    "diff_batch",
    "finalize_review",
    "complete_routed_review",
    "source_fallback_diff",
    "live_diff_contents",
    "live_handle_diff",
    "live_handle_review",
}

_SENTINEL = {"passthrough_sentinel": "core-authored-value"}


# ---------------------------------------------------------------------------
# Recorder over the C-ABI call layer
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for the cdylib: records every op, forwards it, optionally rewrites the
    envelope of chosen ops (``overrides[name](real_envelope) -> envelope``)."""

    def __init__(self, real: Any, overrides: dict[str, Callable[[dict], dict]]) -> None:
        self._real = real
        self._overrides = overrides
        self._buffers: list[Any] = []
        self.calls: list[str] = []
        self.envelopes: dict[str, list[dict]] = {}

    def intentumdiff_call(self, name: bytes, payload: bytes) -> int:
        op = name.decode("utf-8")
        self.calls.append(op)
        ptr = self._real.intentumdiff_call(name, payload)
        try:
            envelope = json.loads(ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8"))
        finally:
            self._real.intentumdiff_free(ptr)
        if op in self._overrides:
            envelope = self._overrides[op](copy.deepcopy(envelope))
        self.envelopes.setdefault(op, []).append(envelope)
        buffer = ctypes.create_string_buffer(json.dumps(envelope).encode("utf-8"))
        self._buffers.append(buffer)
        return ctypes.addressof(buffer)

    def intentumdiff_free(self, _ptr: int) -> None:
        return None  # buffers are owned by the recorder


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Recorder]:
    real = rust_core._c_abi_lib()

    def install(**overrides: Callable[[dict], dict]) -> _Recorder:
        recorder = _Recorder(real, overrides)
        monkeypatch.setattr(rust_core, "_c_abi_lib", lambda: recorder)
        return recorder

    return install


def _with_sentinel(envelope: dict) -> dict:
    assert envelope["ok"], envelope
    result = envelope["result"]
    result["metadata"] = {**result.get("metadata", {}), **_SENTINEL}
    for change in result.get("changes", [])[:1]:
        change["description"] = "core-authored description"
    return envelope


def _failure(kind: str) -> Callable[[dict], dict]:
    return lambda _envelope: {"ok": False, "error_type": kind, "error": f"canned {kind} failure"}


def _dto(result: dict) -> dict:
    return SemanticDiff.model_validate(result).model_dump(mode="json")


# ---------------------------------------------------------------------------
# (a) passthrough
# ---------------------------------------------------------------------------


def test_review_text_returns_the_canned_core_dto_exactly(record) -> None:
    def canned(envelope: dict) -> dict:
        envelope = _with_sentinel(envelope)
        # Core-chosen names and flags must survive untouched.
        envelope["result"]["old_filename"] = "core-chosen-old.txt"
        envelope["result"]["new_filename"] = "core-chosen-new.txt"
        return envelope

    recorder = record(review_text=canned)
    diff = review_text("one\ntwo\n", "one\nthree\n", filename="notes.txt")

    canned_result = recorder.envelopes["review_text"][0]["result"]
    assert recorder.calls == ["review_text"]
    assert diff.model_dump(mode="json") == _dto(canned_result)
    assert diff.old_filename == "core-chosen-old.txt"
    assert diff.metadata["passthrough_sentinel"] == "core-authored-value"


OLD_GO = "package a\n\nfunc f() int { return 1 }\n"
NEW_GO = "package a\n\nfunc f() int { return 2 }\n\nfunc g() int { return 3 }\n"
OLD_PY = "def f():\n    return 1\n"
NEW_PY = "def f():\n    return 2\n\n\ndef g():\n    return 3\n"


def test_routed_diff_strings_preserves_core_authored_content(record) -> None:
    recorder = record(complete_routed_review=_with_sentinel)
    diff = SemanticDiffer().diff_strings(OLD_GO, NEW_GO, "a.go")

    assert "complete_routed_review" in recorder.calls
    assert diff.metadata["passthrough_sentinel"] == "core-authored-value"
    assert diff.changes[0].description == "core-authored description"


@pytest.mark.xfail(
    strict=True,
    reason="THIN_BINDING_AUDIT S3: differ.py still assembles the review from finer ops "
    "(complete_routed_review, then lifecycle/guardrail/content-type ops) and attaches "
    "content_type and engine_telemetry metadata in Python instead of one review_sources call.",
)
def test_routed_diff_strings_returns_the_core_review_unchanged(record) -> None:
    recorder = record(complete_routed_review=_with_sentinel)
    diff = SemanticDiffer().diff_strings(OLD_GO, NEW_GO, "a.go")

    core_review = recorder.envelopes["complete_routed_review"][0]["result"]
    assert diff.model_dump(mode="json") == _dto(core_review)


def _batch_with_sentinel(envelope: dict) -> dict:
    assert envelope["ok"], envelope
    payload = envelope["result"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    for item in payload.get("diffs", []):
        diff = item.get("diff")
        if isinstance(diff, dict):
            diff["metadata"] = {**diff.get("metadata", {}), **_SENTINEL}
    envelope["result"] = payload
    return envelope


def _batch_diff(recorder: _Recorder) -> dict:
    payload = recorder.envelopes["diff_batch"][0]["result"]
    (item,) = payload["diffs"]
    return item["diff"]


def test_certified_batch_diff_strings_preserves_core_authored_content(record) -> None:
    recorder = record(diff_batch=_batch_with_sentinel)
    diff = SemanticDiffer().diff_strings(OLD_PY, NEW_PY, "a.py")

    assert "diff_batch" in recorder.calls
    assert "finalize_review" not in recorder.calls
    assert diff.metadata["passthrough_sentinel"] == "core-authored-value"


@pytest.mark.xfail(
    strict=True,
    reason="THIN_BINDING_AUDIT S4: the certified-batch adapter stamps metadata.rust_core.* "
    "and differ.py adds content_type/lifecycle metadata to the core diff.",
)
def test_certified_batch_diff_strings_returns_the_core_diff_unchanged(record) -> None:
    recorder = record(diff_batch=_batch_with_sentinel)
    diff = SemanticDiffer().diff_strings(OLD_PY, NEW_PY, "a.py")

    assert diff.model_dump(mode="json") == _dto(_batch_diff(recorder))


# ---------------------------------------------------------------------------
# (b) errors surface, with no substitute review op
# ---------------------------------------------------------------------------

_ERROR_KINDS = [
    ("not_found", FileNotFoundError),
    ("internal", RuntimeError),
    ("value_error", ValueError),
    ("bad_request", ValueError),
    ("size_limit", CoreSizeLimitError),
]


@pytest.mark.parametrize(("kind", "exception"), _ERROR_KINDS)
def test_review_text_core_error_raises_mapped_exception(record, kind, exception) -> None:
    recorder = record(review_text=_failure(kind))
    with pytest.raises(exception, match=f"canned {kind} failure"):
        review_text("a\n", "b\n", filename="notes.txt")
    assert recorder.calls == ["review_text"]


@pytest.mark.parametrize(("kind", "_exception"), _ERROR_KINDS)
def test_routed_finalize_error_is_not_replaced_by_another_review(record, kind, _exception) -> None:
    recorder = record(finalize_review=_failure(kind))
    with pytest.raises(RuntimeError, match=f"canned {kind} failure"):
        SemanticDiffer().diff_strings(OLD_GO, NEW_GO, "a.go")
    review_ops = [op for op in recorder.calls if op in _REVIEW_OPS]
    assert review_ops == ["finalize_review"]


@pytest.mark.parametrize(("kind", "_exception"), _ERROR_KINDS)
def test_certified_batch_error_is_not_replaced_by_another_review(record, kind, _exception) -> None:
    recorder = record(diff_batch=_failure(kind))
    with pytest.raises(RuntimeError, match=f"canned {kind} failure"):
        SemanticDiffer().diff_strings(OLD_PY, NEW_PY, "a.py")
    review_ops = [op for op in recorder.calls if op in _REVIEW_OPS]
    assert review_ops == ["diff_batch"]


# ---------------------------------------------------------------------------
# (c) static boundary check
# ---------------------------------------------------------------------------

# Product shells, outside the binding rule for this check (AGENTS.md / audit S5-S10).
_SHELL_PREFIXES = ("cli/", "lsp/", "lsp_server/", "serve/")
_SHELL_FILES = {"live_server.py", "watcher.py", "github_app.py", "testing.py"}

# Current offenders, keyed by (module, kind, enclosing scope). The set may only shrink:
# a new offender fails, and so does an entry that no longer exists (delete it).
_ALLOWED_OFFENDERS = {
    # S3: the certified single-file batch shortcut is still chosen by language in Python.
    ("_differ_gate.py", "language_set", "RUST_CERTIFIED_LANGUAGES"),
    # S3: compile-database context is attached only for C/C++ language ids.
    ("analysis/compile_commands.py", "language_set", "compile_commands_metadata"),
    # Core schema resolution (#307): schema/descriptor load failures turned into decline values.
    ("analysis/user_schemas.py", "except_returns_value", "_load_descriptor_file"),
    ("analysis/user_schemas.py", "except_returns_value", "_validate_descriptor"),
    ("analysis/schema_resolver.py", "except_returns_value", "resolve_schema"),
    # S9: the indexer turns a per-file failure into a result value.
    ("core/indexer.py", "except_returns_value", "_process_one"),
    # S8: plugin metadata/stamp read failures become values.
    ("plugins/adapter.py", "except_returns_value", "_load_language_info"),
    ("plugins/loader.py", "except_returns_value", "_read_stamp"),
    # S4: the commit batch adapter turns native failures into decline attempts.
    ("rust_core.py", "except_returns_value", "try_rust_core_batch_diffs"),
}


def _language_ids() -> set[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["entry-points"]["intentumdiff.parsers"]) | {
        "generic", "python", "dbt", "dbt-sql", "dbt-yaml", "markdown", "text",
    }


def _string_elements(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)) and node.elts:
        values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        if len(values) == len(node.elts) and all(isinstance(v, str) for v in values):
            return values
    return None


def _scope_names(tree: ast.Module) -> dict[ast.AST, str]:
    scopes: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_scope = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                child_scope = child.name
            elif scope == "<module>" and isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if names and names[0].isupper():
                    child_scope = names[0]
            scopes[child] = child_scope
            visit(child, child_scope)

    visit(tree, "<module>")
    return scopes


def _offenders() -> set[tuple[str, str, str]]:
    languages = _language_ids()
    found: set[tuple[str, str, str]] = set()
    for path in sorted(_PACKAGE.rglob("*.py")):
        module = path.relative_to(_PACKAGE).as_posix()
        if module.startswith(_SHELL_PREFIXES) or module in _SHELL_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes = _scope_names(tree)
        for node in ast.walk(tree):
            values = None
            if isinstance(node, ast.Set):
                values = _string_elements(node)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"set", "frozenset"}
                and node.args
                and not isinstance(node.args[0], ast.Set)
            ):
                values = _string_elements(node.args[0])
            if values and any(value.lower() in languages for value in values):
                found.add((module, "language_set", scopes.get(node, "<module>")))
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _returns_value(node):
                found.add((module, "except_returns_value", scopes.get(node, "<module>")))
    return found


def _is_broad(handler: ast.ExceptHandler) -> bool:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return handler.type is None or any(
        isinstance(kind, ast.Name) and kind.id in {"Exception", "BaseException"} for kind in kinds
    )


def _returns_value(handler: ast.ExceptHandler) -> bool:
    return any(
        isinstance(node, ast.Return)
        and node.value is not None
        and not (isinstance(node.value, ast.Constant) and node.value.value is None)
        for node in ast.walk(handler)
    )


def test_no_new_language_sets_or_value_returning_broad_excepts() -> None:
    new = sorted(_offenders() - _ALLOWED_OFFENDERS)
    assert not new, (
        "Python must not choose behaviour by language id or turn any exception into a "
        f"value; move it to intentumdiff-core: {new}"
    )


def test_boundary_allowlist_only_shrinks() -> None:
    stale = sorted(_ALLOWED_OFFENDERS - _offenders())
    assert not stale, f"These offenders are gone; delete them from _ALLOWED_OFFENDERS: {stale}"
