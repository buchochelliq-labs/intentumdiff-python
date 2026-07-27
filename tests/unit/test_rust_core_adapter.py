"""Tests for the native Rust core host adapter."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

import intentdiff.differ as differ_module
import intentdiff.rust_core as rust_core
from intentdiff import DiffConfig, SemanticDiffer
from intentdiff.core.models import (
    ChangeType,
    CommitDiff,
    NodePosition,
    SemanticDiff,
    SemanticNode,
)

# The RUST_ONLY gate forbids the coarse token-level fallback. Tests that deliberately
# drive a synthetic backend into that fallback are skipped in that mode.
_RUST_ONLY = os.getenv("INTENTDIFF_ENFORCE_RUST_ONLY_ENGINE") == "1"


def _python_fallback(
    self: SemanticDiffer,
    old_content: str,
    new_content: str,
    filename: str,
    language_hint: str | None,
    **kwargs: Any,
) -> SemanticDiff:
    return SemanticDiff(old_filename=filename, new_filename=filename, language="python")


def _passthrough_invariances_json(request_json: str) -> str:
    request = json.loads(request_json)
    return json.dumps(
        {
            "changes": request.get("changes") or [],
            "change_groups": [],
            "ignored_style_changes": [],
        }
    )


def _empty_scope_trails_json(request_json: str) -> str:
    json.loads(request_json)
    return json.dumps({})


def test_rust_core_defaults_to_native_first_and_can_be_disabled(monkeypatch: Any) -> None:
    monkeypatch.delenv("INTENTDIFF_RUST_CORE", raising=False)
    monkeypatch.delenv("INTENTDIFF_EXPERIMENTAL_RUST_CORE", raising=False)
    assert DiffConfig().experimental_rust_core is True

    monkeypatch.setenv("INTENTDIFF_RUST_CORE", "0")
    assert DiffConfig().experimental_rust_core is False

    monkeypatch.delenv("INTENTDIFF_RUST_CORE", raising=False)
    monkeypatch.setenv("INTENTDIFF_EXPERIMENTAL_RUST_CORE", "0")
    assert DiffConfig().experimental_rust_core is False

    monkeypatch.setenv("INTENTDIFF_EXPERIMENTAL_RUST_CORE", "1")
    assert DiffConfig().experimental_rust_core is True


def test_rust_core_unavailable_falls_back_to_python(monkeypatch: Any) -> None:
    monkeypatch.setattr(SemanticDiffer, "_run_stages_1_to_11", _python_fallback)

    def missing_backend() -> Any:
        raise ModuleNotFoundError("intentdiff_rust_core")

    monkeypatch.setattr(rust_core, "_load_backend", missing_backend)

    diff = SemanticDiffer(
        DiffConfig(experimental_rust_core=True, profile_phases=True)
    ).diff_strings("old", "new", "example.py")

    assert diff.language == "python"
    assert "rust_core" not in diff.metadata
    assert diff.metadata["phase_timings"]["phases"] == []


def test_rust_core_incomplete_result_falls_back_to_python(monkeypatch: Any) -> None:
    monkeypatch.setattr(SemanticDiffer, "_run_stages_1_to_11", _python_fallback)
    backend = SimpleNamespace(
        version=lambda: "0.1.0",
        supports_language=lambda language: language == "python",
        diff_python_json=lambda *args: SemanticDiff(
            old_filename="example.py",
            new_filename="example.py",
            language="python",
            metadata={"rust_core": {"status": "scaffold"}},
        ).model_dump_json(),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    diff = SemanticDiffer(DiffConfig(experimental_rust_core=True)).diff_strings(
        "old",
        "new",
        "example.py",
    )

    assert diff.language == "python"
    assert "rust_core" not in diff.metadata


def test_rust_core_complete_result_is_used(monkeypatch: Any) -> None:
    backend = SimpleNamespace(
        version=lambda: "0.1.0",
        supports_language=lambda language: language == "python",
        diff_python_json=lambda *args: SemanticDiff(
            old_filename="example.py",
            new_filename="example.py",
            language="python",
            metadata={"rust_core": {"status": "complete"}},
        ).model_dump_json(),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_diff(
        old_content="same",
        new_content="same",
        old_filename="example.py",
        new_filename="example.py",
        language_hint="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert attempt.diff is not None
    assert attempt.diff.metadata["rust_core"]["used"] is True
    assert attempt.diff.metadata["rust_core"]["backend_version"] == "0.1.0"


def test_rust_core_accepts_python_parser_plugin_id(monkeypatch: Any) -> None:
    backend = SimpleNamespace(
        version=lambda: "0.1.0",
        supports_language=lambda language: language == "python",
        diff_python_json=lambda *args: SemanticDiff(
            old_filename="example.py",
            new_filename="example.py",
            language="python",
            metadata={"rust_core": {"status": "complete"}},
        ).model_dump_json(),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_diff(
        old_content="same",
        new_content="same",
        old_filename="example.py",
        new_filename="example.py",
        language_hint="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True


def test_rust_core_cst_complete_result_is_used(monkeypatch: Any) -> None:
    backend = SimpleNamespace(
        version=lambda: "0.1.0",
        supports_language=lambda language: language == "python",
        diff_python_cst_json=lambda *args: SemanticDiff(
            old_filename="example.py",
            new_filename="example.py",
            language="python",
            metadata={"rust_core": {"status": "complete"}},
        ).model_dump_json(),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_cst_diff(
        old_filtered_cst_json='{"type":"module"}',
        new_filtered_cst_json='{"type":"module"}',
        old_raw_source="",
        new_raw_source="",
        old_filename="example.py",
        new_filename="example.py",
        parser_wasm_path="python_parser.wasm",
        language="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert attempt.diff is not None
    assert attempt.diff.metadata["rust_core"]["used"] is True


def test_rust_core_cst_falls_back_for_non_python_language(monkeypatch: Any) -> None:
    backend = SimpleNamespace(
        version=lambda: "0.1.0",
        supports_language=lambda language: language == "python",
        diff_python_cst_json=lambda *args: "should not be called",
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_cst_diff(
        old_filtered_cst_json="{}",
        new_filtered_cst_json="{}",
        old_raw_source="",
        new_raw_source="",
        old_filename="example.go",
        new_filename="example.go",
        parser_wasm_path="go_parser.wasm",
        language="go",
        parser_plugin_id="go",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is False
    assert attempt.fallback_reason == "unsupported language"


def test_rust_core_batch_complete_result_is_used(monkeypatch: Any) -> None:
    diff = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    )
    payload = {
        "schema_version": 1,
        "status": "complete",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "complete", "diff": diff.model_dump(mode="json")}],
        "metadata": {"rust_core_stage": "batch_boundary"},
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_batch_diff(
        old_source="same",
        new_source="same",
        old_filename="example.py",
        new_filename="example.py",
        parser_wasm_path="python_parser.wasm",
        language="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert attempt.diff is not None
    assert attempt.diff.metadata["rust_core"]["used"] is True
    assert attempt.diff.metadata["rust_core"]["engine"] == "rust_core_batch_v4"
    assert attempt.diff.metadata["rust_core"]["backend_version"] == "0.4.0"
    assert attempt.diff.metadata["rust_core"]["cache_policy"] == "python_diff_cache_skipped"
    assert attempt.diff.metadata["engine_owner"] == "rust"
    assert attempt.diff.metadata["semantic_contract"] == "rust_finalized_v1"
    assert attempt.diff.metadata["rust_core_batch"]["rust_core_stage"] == "batch_boundary"
    adapter_phases = {
        phase["name"] for phase in attempt.diff.metadata["rust_core_batch"]["adapter_phase_timings"]
    }
    assert {
        "rust_core_request_construction",
        "rust_core_request_json_encode",
        "rust_core_batch_execution",
        "rust_core_response_json_decode",
        "rust_core_response_validation",
        "rust_core_result_assembly",
    }.issubset(adapter_phases)


def test_rust_core_batch_accepts_canonical_python_parser_id(
    monkeypatch: Any,
) -> None:
    captured_request: dict[str, Any] = {}
    diff = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    )
    payload = {
        "schema_version": 1,
        "status": "complete",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "complete", "diff": diff.model_dump(mode="json")}],
        "metadata": {},
    }

    def _diff_batch(request_json: str) -> str:
        captured_request.update(json.loads(request_json))
        return json.dumps(payload)

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=_diff_batch,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_batch_diff(
        old_source="same",
        new_source="same",
        old_filename="example.py",
        new_filename="example.py",
        parser_wasm_path="python_parser.wasm",
        language="python",
        parser_plugin_id="intentdiff:python:python",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert captured_request["files"][0]["parser_plugin_id"] == "intentdiff:python:python"


def test_rust_core_batch_fallback_item_is_not_used(monkeypatch: Any) -> None:
    payload = {
        "schema_version": 1,
        "status": "fallback",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "fallback", "reason": "changed-file finalization pending"}],
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_batch_diff(
        old_source="old",
        new_source="new",
        old_filename="example.py",
        new_filename="example.py",
        parser_wasm_path="python_parser.wasm",
        language="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is False
    assert attempt.fallback_reason == "changed-file finalization pending"


def test_rust_core_batch_diffs_accepts_multiple_complete_items(
    monkeypatch: Any,
) -> None:
    first = SemanticDiff(
        old_filename="a.py",
        new_filename="a.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    )
    second = SemanticDiff(
        old_filename="b.py",
        new_filename="b.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    )
    payload = {
        "schema_version": 1,
        "status": "complete",
        "engine": "rust_core_batch_v4",
        "diffs": [
            {"index": 0, "status": "complete", "diff": first.model_dump(mode="json")},
            {"index": 1, "status": "complete", "diff": second.model_dump(mode="json")},
        ],
        "metadata": {
            "rust_core_stage": "batch_boundary",
            "batch_size": 2,
            "parallel": True,
            "parallel_workers": 3,
        },
    }
    captured: list[dict[str, Any]] = []

    def diff_batch(request_json: str) -> str:
        captured.append(json.loads(request_json))
        return json.dumps(payload)

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=diff_batch,
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_batch_diffs(
        files=[
            {
                "old_source": "old a",
                "new_source": "new a",
                "old_filename": "a.py",
                "new_filename": "a.py",
                "language": "python",
                "parser_wasm_path": "python_parser.wasm",
            },
            {
                "old_source": "old b",
                "new_source": "new b",
                "old_filename": "b.py",
                "new_filename": "b.py",
                "language": "python",
                "parser_wasm_path": "python_parser.wasm",
            },
        ],
        config=DiffConfig(experimental_rust_core=True),
        parallel=True,
        max_workers=3,
    )

    assert attempt.used is True
    assert sorted(attempt.diffs) == [0, 1]
    assert attempt.diffs[0].metadata["rust_core"]["used"] is True
    assert attempt.diffs[1].metadata["rust_core_batch"]["parallel_workers"] == 3
    adapter_phases = {phase["name"] for phase in attempt.adapter_phase_timings}
    assert {
        "rust_core_request_construction",
        "rust_core_request_json_encode",
        "rust_core_batch_execution",
        "rust_core_response_json_decode",
        "rust_core_response_validation",
        "rust_core_result_assembly",
    }.issubset(adapter_phases)
    assert (
        attempt.diffs[0].metadata["rust_core_batch"]["adapter_phase_timings"]
        == attempt.adapter_phase_timings
    )
    assert captured[0]["mode"] == "commit_batch"
    assert captured[0]["python_parser_backend"] == "native"
    assert captured[0]["parallel"] is True
    assert captured[0]["max_workers"] == 3

    attempt = rust_core.try_rust_core_batch_diffs(
        files=[
            {
                "old_source": "old a",
                "new_source": "new a",
                "old_filename": "a.py",
                "new_filename": "a.py",
                "language": "python",
                "parser_wasm_path": "python_parser.wasm",
            }
        ],
        config=DiffConfig(experimental_rust_core=True),
        python_parser_backend="wasm",
    )

    assert attempt.used is True
    assert captured[1]["python_parser_backend"] == "wasm"


def test_rust_core_batch_diffs_records_per_item_fallback(
    monkeypatch: Any,
) -> None:
    diff = SemanticDiff(
        old_filename="a.py",
        new_filename="a.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    )
    payload = {
        "schema_version": 1,
        "status": "partial",
        "engine": "rust_core_batch_v4",
        "diffs": [
            {"index": 0, "status": "complete", "diff": diff.model_dump(mode="json")},
            {"index": 1, "status": "fallback", "reason": "unsupported language"},
        ],
        "metadata": {"fallback_count": 1},
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_batch_diffs(
        files=[
            {
                "old_source": "old",
                "new_source": "new",
                "old_filename": "a.py",
                "new_filename": "a.py",
                "language": "python",
                "parser_wasm_path": "python_parser.wasm",
            },
            {
                "old_source": "old",
                "new_source": "new",
                "old_filename": "b.go",
                "new_filename": "b.go",
                "language": "go",
                "parser_wasm_path": "go_parser.wasm",
            },
        ],
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert sorted(attempt.diffs) == [0]
    assert attempt.fallback_reasons == {1: "unsupported language"}


def test_rust_core_batch_candidate_item_is_not_used_by_product_adapter(
    monkeypatch: Any,
) -> None:
    diff = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "candidate"}},
    )
    payload = {
        "schema_version": 1,
        "status": "candidate",
        "engine": "rust_core_batch_v4",
        "diffs": [
            {
                "status": "candidate",
                "candidate_diff": diff.model_dump(mode="json"),
                "fallback_reason": "candidate is not parity-certified",
            }
        ],
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_batch_diff(
        old_source="old",
        new_source="new",
        old_filename="example.py",
        new_filename="example.py",
        parser_wasm_path="python_parser.wasm",
        language="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is False
    assert attempt.fallback_reason == "candidate"


def test_rust_core_candidate_batch_returns_validated_payload(monkeypatch: Any) -> None:
    diff = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "candidate"}},
    )
    payload = {
        "schema_version": 1,
        "status": "candidate",
        "engine": "rust_core_batch_v4",
        "diffs": [
            {
                "status": "candidate",
                "candidate_diff": diff.model_dump(mode="json"),
                "candidate_signature": [],
                "phase_timings": [],
                "fallback_reason": "candidate is not parity-certified",
            }
        ],
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.run_rust_core_candidate_batch(
        files=[
            {
                "old_source": "old",
                "new_source": "new",
                "old_filename": "example.py",
                "new_filename": "example.py",
                "language": "python",
                "parser_wasm_path": "python_parser.wasm",
            }
        ],
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert attempt.payload is not None
    assert attempt.payload["diffs"][0]["status"] == "candidate"


def _node(
    node_id: str,
    node_type: str,
    label: str,
    *,
    line: int,
    children: list[SemanticNode] | None = None,
) -> SemanticNode:
    return SemanticNode(
        id=node_id,
        node_type=node_type,
        label=label,
        position=NodePosition(
            start_line=line,
            start_col=0,
            end_line=line,
            end_col=max(len(label), 1),
        ),
        structural_hash=f"hash-{node_id}",
        children=children or [],
    )


def test_rust_core_tree_reconstructs_matching_and_changes(monkeypatch: Any) -> None:
    old_child = _node("old.fn", "function", "total", line=0)
    new_child = _node("new.fn", "function", "sum_total", line=0)
    old_tree = _node("old.root", "module", "module", line=0, children=[old_child])
    new_tree = _node("new.root", "module", "module", line=0, children=[new_child])
    payload = {
        "status": "complete",
        "engine": "rust_core_semantic_tree_v2_stage11",
        "changes": [
            {
                "change_type": "MODIFICATION",
                "old_node": old_child.model_dump(mode="json"),
                "new_node": new_child.model_dump(mode="json"),
            }
        ],
        "matching_pairs": [
            {"old_id": "old.root", "new_id": "new.root"},
            {"old_id": "old.fn", "new_id": "new.fn"},
        ],
        "metadata": {"old_nodes": 2, "new_nodes": 2},
    }
    backend = SimpleNamespace(
        version=lambda: "0.2.0",
        supports_language=lambda language: language == "python",
        diff_python_semantic_tree_json=lambda *args: payload,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_tree_diff(
        old_tree=old_tree,
        new_tree=new_tree,
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert attempt.matching is not None
    assert [(pair.old_node.id, pair.new_node.id) for pair in attempt.matching] == [
        ("old.root", "new.root"),
        ("old.fn", "new.fn"),
    ]
    assert attempt.changes is not None
    assert attempt.changes[0].change_type == ChangeType.MODIFICATION
    assert attempt.changes[0].old_node is old_child
    assert attempt.changes[0].new_node is new_child
    assert attempt.metadata is not None
    assert attempt.metadata["rust_core"]["used"] is True
    assert attempt.metadata["rust_core"]["backend_version"] == "0.2.0"


def test_rust_core_tree_invalid_matching_id_falls_back(monkeypatch: Any) -> None:
    old_tree = _node("old.root", "module", "module", line=0)
    new_tree = _node("new.root", "module", "module", line=0)
    backend = SimpleNamespace(
        version=lambda: "0.2.0",
        supports_language=lambda language: language == "python",
        diff_python_semantic_tree_json=lambda *args: {
            "status": "complete",
            "engine": "rust_core_semantic_tree_v2_stage11",
            "changes": [],
            "matching_pairs": [{"old_id": "missing", "new_id": "new.root"}],
            "metadata": {},
        },
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_tree_diff(
        old_tree=old_tree,
        new_tree=new_tree,
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is False
    assert "unknown node id" in attempt.fallback_reason


def test_rust_core_tree_diagnostics_fall_back_before_backend(monkeypatch: Any) -> None:
    old_tree = _node("old.root", "module", "module", line=0)
    new_tree = _node("new.root", "module", "module", line=0)

    def fail_backend() -> Any:
        raise AssertionError("backend should not be loaded")

    monkeypatch.setattr(rust_core, "_load_backend", fail_backend)

    attempt = rust_core.try_rust_core_tree_diff(
        old_tree=old_tree,
        new_tree=new_tree,
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        config=DiffConfig(experimental_rust_core=True, diagnostics=True),
    )

    assert attempt.used is False
    assert attempt.fallback_reason == "diagnostics require Python pipeline"


def test_rust_core_source_stage_reconstructs_stage11_payload(monkeypatch: Any) -> None:
    old_tree = _node("old.root", "module", "module", line=0)
    new_tree = _node("new.root", "module", "module", line=0)
    payload = {
        "status": "complete",
        "engine": "rust_core_sources_v3_stage11",
        "old_tree": old_tree.model_dump(mode="json"),
        "new_tree": new_tree.model_dump(mode="json"),
        "changes": [],
        "matching_pairs": [{"old_id": "old.root", "new_id": "new.root"}],
        "adaptive_fuel": 123,
        "metadata": {
            "engine": "rust_core_sources_v3_stage11",
            "old_nodes": 1,
            "new_nodes": 1,
            "matching_pairs": 1,
            "wasm_boundary": "rust_wasmtime",
        },
    }
    backend = SimpleNamespace(
        version=lambda: "0.3.0",
        supports_language=lambda language: language == "python",
        diff_python_sources_stage11_json=lambda *args: payload,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_sources_stage11(
        old_source="def old():\n    pass\n",
        new_source="def new():\n    pass\n",
        old_filename="example.py",
        new_filename="example.py",
        parser_wasm_path="python_parser.wasm",
        language="python",
        parser_plugin_id="python-parser",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert attempt.old_tree == old_tree
    assert attempt.new_tree == new_tree
    assert attempt.adaptive_fuel == 123
    assert attempt.matching is not None
    assert [(pair.old_node.id, pair.new_node.id) for pair in attempt.matching] == [
        ("old.root", "new.root")
    ]
    assert attempt.metadata is not None
    assert attempt.metadata["rust_core"]["engine"] == "rust_core_sources_v3_stage11"
    assert attempt.metadata["rust_core"]["backend_version"] == "0.3.0"


def test_semantic_differ_uses_rust_batch_metadata(monkeypatch: Any) -> None:
    diff_payload = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    ).model_dump(mode="json")
    payload = {
        "schema_version": 1,
        "status": "complete",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "complete", "diff": diff_payload}],
        "metadata": {
            "rust_core_stage": "batch_boundary",
            "phase_timings": [{"name": "rust_batch_request_decode", "duration_ms": 1.0}],
        },
    }
    captured: list[dict[str, Any]] = []

    def diff_batch(request_json: str) -> str:
        captured.append(json.loads(request_json))
        return json.dumps(payload)

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=diff_batch,
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    diff = SemanticDiffer(
        DiffConfig(experimental_rust_core=True, profile_phases=True)
    ).diff_strings(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    return sum(items)\n",
        "example.py",
        language_hint="python",
    )

    assert diff.metadata["rust_core"]["used"] is True
    assert diff.metadata["rust_core"]["engine"] == "rust_core_batch_v4"
    assert diff.metadata["rust_core"]["stage"] == "batch_final_diff"
    assert captured[0]["python_parser_backend"] == "native"
    phases = [phase["name"] for phase in diff.metadata["phase_timings"]["phases"]]
    assert "rust_core_batch_execution" in phases


def test_semantic_differ_uses_rust_batch_for_changed_complete_output(
    monkeypatch: Any,
) -> None:
    diff_payload = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    ).model_dump(mode="json")
    payload = {
        "schema_version": 1,
        "status": "complete",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "complete", "diff": diff_payload}],
        "metadata": {
            "rust_core_stage": "batch_boundary",
            "phase_timings": [{"name": "rust_batch_request_decode", "duration_ms": 1.0}],
        },
    }
    captured: list[dict[str, Any]] = []

    def diff_batch(request_json: str) -> str:
        captured.append(json.loads(request_json))
        return json.dumps(payload)

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=diff_batch,
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    diff = SemanticDiffer(
        DiffConfig(experimental_rust_core=True, profile_phases=True)
    ).diff_strings(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    return sum(item for item in items)\n",
        "example.py",
        language_hint="python",
    )

    assert diff.metadata["rust_core"]["used"] is True
    assert diff.metadata["rust_core"]["engine"] == "rust_core_batch_v4"
    assert diff.metadata["rust_core"]["cache_policy"] == "python_diff_cache_skipped"
    assert captured[0]["python_parser_backend"] == "native"
    phases = [phase["name"] for phase in diff.metadata["phase_timings"]["phases"]]
    assert "rust_core_batch_execution" in phases
    assert "matching" not in phases


def test_rust_core_commit_json_adapter_keeps_commit_bytes_lazy(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("INTENTDIFF_RUST_CORE_VALIDATE_JSON", raising=False)
    diff_payload = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
        metadata={"rust_core": {"status": "complete"}},
    ).model_dump(mode="json")
    commit_payload = json.dumps(
        {
            "old_ref": "HEAD",
            "new_ref": "",
            "guardrail_violations": [],
            "file_diffs": [diff_payload],
            "cross_file_changes": [],
            "parse_errors": [],
        }
    ).encode("utf-8")
    captured: list[dict[str, Any]] = []

    def fail_hot_path_validation(cls: type[CommitDiff], payload: bytes) -> CommitDiff:
        raise AssertionError("CommitDiff validation should be lazy on the fast path")

    monkeypatch.setattr(
        CommitDiff,
        "model_validate_json",
        classmethod(fail_hot_path_validation),
    )

    def diff_batch_commit_json(request_json: str) -> tuple[str, bytes]:
        captured.append(json.loads(request_json))
        return (
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "complete",
                    "certification": "python_native_v4kb",
                    "phase_timings": [
                        {
                            "name": "rust_commit_json_output_validation",
                            "duration_ms": 0.1,
                        }
                    ],
                    "commit_diff_byte_size": len(commit_payload),
                    "semantic_signature_hash": "abc123",
                }
            ),
            commit_payload,
        )

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch_commit_json=diff_batch_commit_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_commit_json(
        files=[
            {
                "old_source": "def total():\n    return 1\n",
                "new_source": "def total():\n    return 2\n",
                "old_filename": "example.py",
                "new_filename": "example.py",
                "language": "python",
                "parser_plugin_id": "python",
                "parser_wasm_path": "python_parser.wasm",
            }
        ],
        old_ref="HEAD",
        new_ref="",
        config=DiffConfig(experimental_rust_core=True),
        parallel=True,
        max_workers=2,
    )

    assert attempt.used is True
    assert attempt.commit_diff_json == commit_payload
    assert attempt.control["semantic_signature_hash"] == "abc123"
    assert captured[0]["mode"] == "commit_json"
    assert captured[0]["python_parser_backend"] == "native"
    assert captured[0]["parallel"] is True
    assert captured[0]["max_workers"] == 2
    phase_names = [phase["name"] for phase in attempt.adapter_phase_timings]
    assert "rust_core_commit_pyo3_call" in phase_names
    assert next(
        phase
        for phase in attempt.adapter_phase_timings
        if phase["name"] == "rust_core_commit_pyo3_call"
    )["summary_exclude"] is True
    assert "rust_core_commit_debug_pydantic_validation" not in phase_names


def test_rust_core_commit_json_lazy_wrapper_validates_on_demand() -> None:
    diff_payload = SemanticDiff(
        old_filename="example.py",
        new_filename="example.py",
        language="python",
    ).model_dump(mode="json")
    attempt = rust_core.RustCoreCommitJsonAttempt(
        commit_diff_json=json.dumps(
            {
                "old_ref": "HEAD",
                "new_ref": "",
                "guardrail_violations": [],
                "file_diffs": [diff_payload],
                "cross_file_changes": [],
                "parse_errors": [],
            }
        ).encode("utf-8")
    )

    commit = attempt.to_model()

    assert commit.old_ref == "HEAD"
    assert commit.file_diffs[0].new_filename == "example.py"


def test_rust_core_commit_json_adapter_falls_back_on_control_status(
    monkeypatch: Any,
) -> None:
    def diff_batch_commit_json(request_json: str) -> tuple[str, None]:
        return (
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "fallback",
                    "reason": "mixed-language batch",
                    "certification": "",
                }
            ),
            None,
        )

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch_commit_json=diff_batch_commit_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_commit_json(
        files=[
            {
                "old_source": "",
                "new_source": "",
                "old_filename": "example.py",
                "new_filename": "example.py",
                "language": "python",
                "parser_plugin_id": "python",
                "parser_wasm_path": "python_parser.wasm",
            }
        ],
        old_ref="HEAD",
        new_ref="",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is False
    assert attempt.fallback_reason == "mixed-language batch"
    assert attempt.control["status"] == "fallback"


def test_rust_core_commit_json_adapter_rejects_byte_size_mismatch(
    monkeypatch: Any,
) -> None:
    payload = b'{"old_ref":"HEAD","new_ref":"","file_diffs":[]}'

    def diff_batch_commit_json(request_json: str) -> tuple[str, bytes]:
        return (
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "complete",
                    "certification": "python_native_v4kb",
                    "commit_diff_byte_size": len(payload) + 1,
                }
            ),
            payload,
        )

    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch_commit_json=diff_batch_commit_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    attempt = rust_core.try_rust_core_commit_json(
        files=[
            {
                "old_source": "",
                "new_source": "",
                "old_filename": "example.py",
                "new_filename": "example.py",
                "language": "python",
                "parser_plugin_id": "python",
                "parser_wasm_path": "python_parser.wasm",
            }
        ],
        old_ref="HEAD",
        new_ref="",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is False
    assert "byte size mismatch" in attempt.fallback_reason


@pytest.mark.skipif(
    _RUST_ONLY,
    reason=(
        "Exercises the coarse token-level fallback (synthetic backend exposes no "
        "finalize_review_json -> rust_finalize_declined), which the RUST_ONLY gate forbids."
    ),
)
def test_semantic_differ_batch_fallback_continues_python_pipeline(monkeypatch: Any) -> None:
    payload = {
        "schema_version": 1,
        "status": "fallback",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "fallback", "reason": "changed-file finalization pending"}],
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    diff = SemanticDiffer(
        DiffConfig(experimental_rust_core=True, profile_phases=True)
    ).diff_strings(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    return sum(item for item in items)\n",
        "example.py",
        language_hint="python",
    )

    assert "rust_core" not in diff.metadata
    assert diff.has_semantic_changes is True
    phases = [phase["name"] for phase in diff.metadata["phase_timings"]["phases"]]
    assert "rust_core_batch_execution" in phases
    # Post-retirement degradation chain (issue #57 payoff, stage 4b): batch declined ->
    # the per-stage Rust finalize is attempted next; this synthetic backend exposes no
    # finalize_review_json, so the pipeline degrades to the coarse token-level diff
    # (the python stages are retired). A REAL backend serves at the finalize tier.
    assert "rust_finalize_review" in phases
    assert diff.metadata.get("fallback_reason") == "rust_finalize_declined"


def test_semantic_differ_skips_rust_batch_when_guardrails_may_apply(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(differ_module, "guardrails_may_apply", lambda *args: True)

    def fail_rust_call(**kwargs: Any) -> Any:
        raise AssertionError("Rust core should be skipped when guardrails may apply")

    monkeypatch.setattr(differ_module, "try_rust_core_batch_diff", fail_rust_call)

    diff = SemanticDiffer(
        DiffConfig(experimental_rust_core=True, profile_phases=True)
    ).diff_strings(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    return sum(item for item in items)\n",
        "example.py",
        language_hint="python",
    )

    phases = [phase["name"] for phase in diff.metadata["phase_timings"]["phases"]]
    assert "rust_core_batch_execution" not in phases
    # The point of this test is that the certified *batch* shortcut is skipped so that
    # guardrails are EVALUATED. The serving tier may be the full per-stage pipeline
    # ("matching" pure-Python GumTree or "rust_matching" after the matcher migration) or,
    # since the issue #57 python flip, the per-stage Rust finalize routing — which runs
    # apply_guardrails_to_diff on the routed diff before returning. All three evaluate
    # guardrails; only the batch shortcut does not.
    rust_core_meta = diff.metadata.get("rust_core")
    if rust_core_meta is not None:
        assert rust_core_meta.get("stage") == "per_stage_finalize_routing"
        assert "rust_finalize_review" in phases
    else:
        assert any(name in phases for name in ("matching", "rust_matching"))


def test_semantic_differ_skips_rust_batch_when_python_enricher_is_registered(
    monkeypatch: Any,
) -> None:
    def fail_rust_call(**kwargs: Any) -> Any:
        raise AssertionError("Rust core should be skipped when enrichers are registered")

    monkeypatch.setattr(differ_module, "try_rust_core_batch_diff", fail_rust_call)
    differ = SemanticDiffer(DiffConfig(experimental_rust_core=True, profile_phases=True))
    enricher = SimpleNamespace(
        provenance="test-enricher",
        enrich=lambda tree_json, *args, **kwargs: tree_json,
    )
    monkeypatch.setattr(differ._registry, "get_enrichers", lambda language: [enricher])
    monkeypatch.setattr(differ._registry, "get_diff_analyzers", lambda language: [])

    diff = differ.diff_strings(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    return sum(item for item in items)\n",
        "example.py",
        language_hint="python",
    )

    # The batch shortcut must be skipped (it bypasses tree construction, so the
    # enricher could never run); the serving tier may be the python pipeline OR the
    # per-stage Rust finalize routing (issue #57 payoff) — enrichment runs at stage 7b
    # BEFORE the routing gate, so enriched trees flow through routing.
    phases = [phase["name"] for phase in diff.metadata["phase_timings"]["phases"]]
    assert "rust_core_batch_execution" not in phases
    assert "enricher_execution" in phases
    rust_core_meta = diff.metadata.get("rust_core")
    if rust_core_meta is not None:
        assert rust_core_meta.get("stage") == "per_stage_finalize_routing"


def test_semantic_differ_skips_rust_batch_when_python_diff_analyzer_is_registered(
    monkeypatch: Any,
) -> None:
    def fail_rust_call(**kwargs: Any) -> Any:
        raise AssertionError("Rust core should be skipped when diff analyzers are registered")

    monkeypatch.setattr(differ_module, "try_rust_core_batch_diff", fail_rust_call)
    differ = SemanticDiffer(DiffConfig(experimental_rust_core=True, profile_phases=True))
    analyzer = SimpleNamespace(
        provenance="test-analyzer",
        analyze_diff=lambda diff_json, *args, **kwargs: diff_json,
    )
    monkeypatch.setattr(differ._registry, "get_enrichers", lambda language: [])
    monkeypatch.setattr(differ._registry, "get_diff_analyzers", lambda language: [analyzer])

    diff = differ.diff_strings(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    return sum(item for item in items)\n",
        "example.py",
        language_hint="python",
    )

    # The batch shortcut must be skipped (it produces final output without the
    # analyzer hook); the serving tier may be the python pipeline (stage 13.5) OR the
    # per-stage Rust finalize routing (issue #57 payoff), which runs the analyzers on
    # the ROUTED diff under the same "diff_analyzers" phase.
    phases = [phase["name"] for phase in diff.metadata["phase_timings"]["phases"]]
    assert "rust_core_batch_execution" not in phases
    assert "diff_analyzers" in phases
    rust_core_meta = diff.metadata.get("rust_core")
    if rust_core_meta is not None:
        assert rust_core_meta.get("stage") == "per_stage_finalize_routing"
