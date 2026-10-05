from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import DiffConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
STRICT_GATE_ENV = "INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE"

_PYTHON_ENGINE_IMPORT_RE = re.compile(
    r"(^|\s)(from|import)\s+("
    r"intentumdiff\.analysis"
    r"|intentumdiff\.core\.engine"
    r")\b",
    re.MULTILINE,
)

# Split-repo boundary ratchet: only imports of Python analysis modules / the retired
# Python engine count here. Parser-source and tree-sitter ownership live outside this binding repo.
_KNOWN_PYTHON_ENGINE_DEBT_PATHS = {
    # Schema detection/registration/descriptor-validation shell (issue #63): the resolver and
    # the user-schema registry cross-import within intentumdiff.analysis. No matching-engine
    # logic — identity fields are marshaled into the Rust core's keyed matching.
    "src/intentumdiff/analysis/schema_resolver.py",
    "src/intentumdiff/analysis/user_schemas.py",
    # differ.py's DTO/presentation + telemetry helper families (issue #81 split):
    # _differ_presentation imports analysis.text_review (line-review presentation assembly;
    # the review compute itself is Rust — text_review_generic.rs); _differ_runtime imports
    # analysis.diagnostics (the DiagnosticsRecorder telemetry sink).
    "src/intentumdiff/_differ_presentation.py",
    "src/intentumdiff/_differ_runtime.py",
    # Guardrail orchestration/reporting: the RULE EVALUATION is Rust (A1.3,
    # evaluate_guardrail_rules); this marshals trees + reads the keyed_profiles /
    # resource_profiles language-set CONSTANTS and builds the report DTOs.
    "src/intentumdiff/analysis/guardrails.py",
    # CLI package (issue #80 split): imports analysis.guardrail_reports (report formatting).
    "src/intentumdiff/cli/_commands.py",
    "src/intentumdiff/cli/_shared.py",
    # Commit orchestration: imports analysis.cross_file, whose detect_cross_file_changes is a
    # thin marshal into the Rust core (try_rust_diff_symbol_tables) — no Python engine.
    "src/intentumdiff/core/commit_differ.py",
    # The public API / VCS / config / orchestration facade — imports the analysis SHELL
    # submodules above (compile_commands metadata, diagnostics, guardrails, schema_resolver,
    # text_review, user_schemas). Every processing call inside routes to the Rust core.
    "src/intentumdiff/differ.py",
}

_KNOWN_PYTHON_TREE_SITTER_DEPS: set[str] = set()


def test_issue_specific_engine_helpers_stay_out_of_python_layer() -> None:
    banned_helpers = [
        REPO_ROOT / "src" / "intentumdiff" / "analysis" / "cpp_preprocessor.py",
        REPO_ROOT / "src" / "intentumdiff" / "analysis" / "scopes.py",
    ]

    assert [path for path in banned_helpers if path.exists()] == []


def test_public_paths_do_not_execute_python_invariance_module() -> None:
    public_paths = [
        REPO_ROOT / "src" / "intentumdiff" / "differ.py",
    ]

    offenders = [
        _relative(path)
        for path in public_paths
        if "intentumdiff.analysis.invariances" in path.read_text(encoding="utf8")
    ]

    assert offenders == []


def test_certified_python_public_path_uses_rust_finalizer_not_python_engine(
    monkeypatch,
) -> None:
    # The python engine symbols this test used to monkeypatch-fail
    # (refine_changes / detect_refactorings / normalize_for_review / classify) were
    # DELETED with the transitional layer (issue #57 payoff, stage 4b) — the guarantee
    # is structural now; the assertions pin which Rust contract serves the path.
    del monkeypatch
    diff = SemanticDiffer(
        DiffConfig(
            experimental_rust_core=True,
            extra_trivia_types=["__intentumdiff_test_noop__"],
        )
    ).diff_strings(
        "def answer():\n    return 1\n",
        "def answer():\n    return 1\n",
        "example.py",
        language_hint="python",
    )

    # Issue #57 python flip: a non-default config makes the certified BATCH path
    # decline, and the tier behind it is now the full-Rust 9-fin finalize routing
    # (semantic_contract rust_finalize_review_v1), not the stage-11 hybrid that still
    # ran python promote_moves. Either Rust contract satisfies the boundary; the
    # monkeypatched failers above remain the real teeth (no python engine, ever).
    assert diff.metadata["semantic_contract"] in {
        "rust_finalized_v1",
        "rust_finalize_review_v1",
    }
    assert diff.metadata["engine_owner"] == "rust"
    assert diff.has_semantic_changes is False


def test_certified_python_change_path_uses_rust_finalizer_not_python_engine(
    monkeypatch,
) -> None:
    # Same structural guarantee as the public-path test: the python engine is deleted.
    del monkeypatch
    diff = SemanticDiffer(DiffConfig(experimental_rust_core=True)).diff_strings(
        "def answer():\n    return 1\n",
        "def answer():\n    return 2\n",
        "example.py",
        language_hint="python",
    )

    assert diff.metadata["semantic_contract"] == "rust_finalized_v1"
    assert diff.metadata["engine_owner"] == "rust"
    assert diff.has_semantic_changes is True
    assert diff.changes


def test_strict_rust_gate_allows_batch_decline_to_native_finalize(monkeypatch) -> None:
    # A declined certified BATCH is a Rust->Rust transition, not a Python fallback: the
    # single-file path falls through to the native per-stage Rust finalize routing, which
    # serves the diff. The RUST_ONLY gate must NOT fire on a batch decline — it fires only
    # at a genuine token-level fallback (see test_strict_rust_gate_blocks_token_fallback).
    import intentumdiff.differ as differ_module
    from intentumdiff.rust_core import RustCoreBatchAttempt

    monkeypatch.setenv(STRICT_GATE_ENV, "1")
    monkeypatch.setattr(
        differ_module,
        "try_rust_core_batch_diff",
        lambda **_kwargs: RustCoreBatchAttempt(fallback_reason="no rust support"),
    )

    diff = SemanticDiffer(DiffConfig(experimental_rust_core=True)).diff_strings(
        "def answer():\n    return 1\n",
        "def answer():\n    return 2\n",
        "example.py",
        language_hint="python",
    )

    assert diff.metadata["engine_owner"] == "rust"
    assert diff.metadata["semantic_contract"] in {
        "rust_finalized_v1",
        "rust_finalize_review_v1",
    }
    assert diff.has_semantic_changes is True


def test_strict_rust_gate_blocks_commit_token_fallback(monkeypatch) -> None:
    # The commit fan-out re-runs every source through the native single-file path. With the
    # Rust core disabled (kill switch) that path reaches the token-level fallback, which the
    # RUST_ONLY gate blocks — and the fan-out re-raises the RustOnlyGateError instead of
    # swallowing it as an ordinary per-file error (a swallow would silently drop the file).
    import intentumdiff.differ as differ_module

    monkeypatch.setenv(STRICT_GATE_ENV, "1")
    changed_files = [
        ("def f():\n    return 1\n", "def f():\n    return 2\n", "a.py", "a.py", None),
    ]

    with (
        patch("git.Repo", return_value=MagicMock()),
        patch(
            "intentumdiff.sources.git_source.iter_changed_sources",
            return_value=iter(changed_files),
        ),
        pytest.raises(
            RuntimeError,
            match=r"Rust-only engine gate prevented fallback: rust core disabled",
        ),
    ):
        differ_module.SemanticDiffer(
            DiffConfig(experimental_rust_core=False)
        ).diff_commit(".")


def test_strict_rust_gate_allows_commit_batch_decline_to_native(monkeypatch) -> None:
    # A commit whose per-file certified-batch attempts decline re-runs each source through
    # the native per-stage Rust finalize path. That is Rust->Rust, not a Python fallback, so
    # the RUST_ONLY gate does NOT fire: the commit completes with every file served natively.
    import intentumdiff.differ as differ_module

    monkeypatch.setenv(STRICT_GATE_ENV, "1")
    changed_files = [
        ("def f():\n    return 1\n", "def f():\n    return 2\n", "a.py", "a.py", None),
        ("y = 1\n", "y = 2\n", "b.py", "b.py", None),
    ]

    with (
        patch("git.Repo", return_value=MagicMock()),
        patch(
            "intentumdiff.sources.git_source.iter_changed_sources",
            return_value=iter(changed_files),
        ),
    ):
        diffs = differ_module.SemanticDiffer(
            DiffConfig(experimental_rust_core=True)
        ).diff_commit(".")

    assert {d.new_filename for d in diffs} == {"a.py", "b.py"}
    assert all(not d.is_fallback for d in diffs)


def test_strict_rust_gate_accepts_rust_source_fallback(monkeypatch) -> None:
    from contextlib import closing
    monkeypatch.setenv(STRICT_GATE_ENV, "1")
    with closing(SemanticDiffer(DiffConfig())) as differ:
        diff = differ.diff_strings("def broken(", "def broken(x", "broken.py", language_hint="python")
    assert diff.is_fallback
    assert diff.metadata["engine_owner"] == "rust"
    assert diff.has_semantic_changes


def _strict_gate_enabled() -> bool:
    return os.environ.get(STRICT_GATE_ENV) == "1"


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()



def test_python_engine_dependency_debt_is_ratcheted() -> None:
    current_debt = {
        _relative(path)
        for path in (REPO_ROOT / "src" / "intentumdiff").rglob("*.py")
        if _PYTHON_ENGINE_IMPORT_RE.search(path.read_text(encoding="utf8"))
    }

    assert current_debt <= _KNOWN_PYTHON_ENGINE_DEBT_PATHS


def test_python_tree_sitter_runtime_dependencies_are_ratcheted() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    current_deps = {
        line.strip().strip('",').split(">=", maxsplit=1)[0]
        for line in pyproject.splitlines()
        if line.strip().startswith('"tree-sitter')
    }

    if _strict_gate_enabled():
        assert current_deps == set()
    else:
        assert current_deps <= _KNOWN_PYTHON_TREE_SITTER_DEPS

