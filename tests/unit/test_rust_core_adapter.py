"""Tests for the native Rust core host adapter."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

import intentumdiff.differ as differ_module
import intentumdiff.rust_core as rust_core
from intentumdiff import DiffConfig, SemanticDiffer
from intentumdiff.core.models import (
    ChangeType,
    CommitDiff,
    NodePosition,
    SemanticDiff,
    SemanticNode,
)

# The RUST_ONLY gate forbids the coarse token-level fallback. Tests that deliberately
# drive a synthetic backend into that fallback are skipped in that mode.
_RUST_ONLY = os.getenv("INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE") == "1"


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
    return json.dumps({"scope_trails": {"old": [], "new": []}})


def test_rust_core_defaults_to_native_first_and_can_be_disabled(monkeypatch: Any) -> None:
    monkeypatch.delenv("INTENTUMDIFF_RUST_CORE", raising=False)
    monkeypatch.delenv("INTENTUMDIFF_EXPERIMENTAL_RUST_CORE", raising=False)
    assert DiffConfig().experimental_rust_core is True

    monkeypatch.setenv("INTENTUMDIFF_RUST_CORE", "0")
    assert DiffConfig().experimental_rust_core is False

    monkeypatch.delenv("INTENTUMDIFF_RUST_CORE", raising=False)
    monkeypatch.setenv("INTENTUMDIFF_EXPERIMENTAL_RUST_CORE", "0")
    assert DiffConfig().experimental_rust_core is False

    monkeypatch.setenv("INTENTUMDIFF_EXPERIMENTAL_RUST_CORE", "1")
    assert DiffConfig().experimental_rust_core is True


def test_rust_core_unavailable_raises_required_engine_error(monkeypatch: Any) -> None:
    # Even a stubbed pipeline result cannot turn a missing required engine into success.
    monkeypatch.setattr(SemanticDiffer, "_run_stages_1_to_11", _python_fallback)

    def missing_backend() -> Any:
        raise ModuleNotFoundError("intentumdiff_rust_core")

    monkeypatch.setattr(rust_core, "_load_backend", missing_backend)
    with pytest.raises(RuntimeError, match="detect_content_type_json.*intentumdiff_rust_core") as error:
        SemanticDiffer(DiffConfig(experimental_rust_core=True)).diff_strings(
            "old", "new", "example.py"
        )
    assert isinstance(error.value.__cause__, ModuleNotFoundError)


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
        parser_plugin_id="intentumdiff:python:python",
        config=DiffConfig(experimental_rust_core=True),
    )

    assert attempt.used is True
    assert captured_request["files"][0]["parser_plugin_id"] == "intentumdiff:python:python"


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
        request = json.loads(request_json)
        captured.append(request)
        # The same backend fake serves a two-file and a one-file request.
        # Return only requested items, preserving strict response-index validation.
        count = len(request["files"])
        response = {
            **payload,
            "diffs": payload["diffs"][:count],
            "metadata": {**payload["metadata"], "batch_size": count},
        }
        return json.dumps(response)

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


def _batch_mapping_fixture(
    monkeypatch: Any, indices: list[Any], *, filenames: tuple[str, str] = ("a.py", "b.py")
) -> None:
    """Install a fake native batch that preserves each item's requested identity."""
    diffs = [
        SemanticDiff(old_filename=name, new_filename=name, language="python")
        for name in filenames
    ]
    payload = {
        "engine": "rust_core_batch_v4",
        "diffs": [
            {"index": indices[0], "status": "complete", "diff": diffs[0].model_dump(mode="json")},
            {"index": indices[1], "status": "complete", "diff": diffs[1].model_dump(mode="json")},
        ],
    }
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda request: json.dumps(payload),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)


def _two_python_batch_inputs() -> list[dict[str, str]]:
    return [
        {
            "old_source": "old",
            "new_source": "new",
            "old_filename": name,
            "new_filename": name,
            "language": "python",
            "parser_wasm_path": "python_parser.wasm",
        }
        for name in ("a.py", "b.py")
    ]


def test_rust_batch_out_of_order_explicit_index_zero_is_not_remapped(
    monkeypatch: Any,
) -> None:
    """Rust may return index 0 second; its diff must remain attached to source 0."""
    _batch_mapping_fixture(monkeypatch, [1, 0], filenames=("b.py", "a.py"))
    attempt = rust_core.try_rust_core_batch_diffs(
        files=_two_python_batch_inputs(),
        config=DiffConfig(experimental_rust_core=True),
    )
    assert attempt.used is True
    assert {index: diff.new_filename for index, diff in attempt.diffs.items()} == {
        0: "a.py",
        1: "b.py",
    }


@pytest.mark.parametrize(
    "indices",
    [[0, 0], [0, 2], [0, "1"], [0, True]],
    ids=["duplicate", "out-of-range", "string", "boolean"],
)
def test_rust_batch_invalid_response_indexes_fail_closed(
    monkeypatch: Any, indices: list[Any],
) -> None:
    _batch_mapping_fixture(monkeypatch, indices)
    attempt = rust_core.try_rust_core_batch_diffs(
        files=_two_python_batch_inputs(),
        config=DiffConfig(experimental_rust_core=True),
    )
    assert attempt.used is False
    assert not attempt.diffs
    assert attempt.fallback_reason.startswith("invalid output:")


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


@pytest.mark.parametrize(
    ("handler", "call"),
    [
        ("generic_text_review_json", lambda root: rust_core.try_rust_generic_text_review("old", "new", 0)),
        ("markdown_section_review_json", lambda root: rust_core.try_rust_markdown_section_review("old", "new")),
        (
            "finalize_review_json",
            lambda root: rust_core.try_rust_finalize_review(
                old_tree=root,
                new_tree=root,
                old_source="old",
                new_source="new",
                language="python",
                config=DiffConfig(),
            ),
        ),
        (
            "enrich_profile_labels_json",
            lambda root: rust_core.try_rust_profile_label_enrichment(root, "source", "python"),
        ),
    ],
)
def test_optional_rust_adapters_raise_on_engine_failure(
    monkeypatch: Any,
    handler: str,
    call: Any,
) -> None:
    root = _node("root", "module", "module", line=0)

    def fail(*args: Any, **kwargs: Any) -> str:
        raise OSError("engine transport failed")

    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(**{handler: fail}))

    with pytest.raises(RuntimeError, match="failed"):
        call(root)


@pytest.mark.parametrize(
    ("handler", "call"),
    [
        ("markdown_section_review_json", lambda root: rust_core.try_rust_markdown_section_review("old", "new")),
        (
            "finalize_review_json",
            lambda root: rust_core.try_rust_finalize_review(
                old_tree=root,
                new_tree=root,
                old_source="old",
                new_source="new",
                language="python",
                config=DiffConfig(),
            ),
        ),
    ],
)
def test_required_rust_review_adapters_fail_closed_when_handler_is_absent(
    monkeypatch: Any,
    handler: str,
    call: Any,
) -> None:
    root = _node("root", "module", "module", line=0)
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="required Rust"):
        call(root)




def test_generic_text_review_missing_handler_fails_closed(monkeypatch: Any) -> None:
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="required Rust generic-text review handler"):
        rust_core.try_rust_generic_text_review("before", "after", 1)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"used": False},
        {"used": False, "reason": "backend unavailable"},
        {"used": True},
        {"used": True, "changes": None},
        {"used": True, "changes": []},  # Rust must include the group field, even if null.
        {"used": True, "changes": [], "group": {}},  # Malformed object must not bypass DTO validation.
        {"used": True, "changes": [], "group": []},
        [],
    ],
)
def test_generic_text_review_rejects_uncertified_or_invalid_payload(
    monkeypatch: Any, payload: Any
) -> None:
    def handler(*_args: Any) -> str:
        return json.dumps(payload)

    monkeypatch.setattr(
        rust_core, "_load_backend", lambda: SimpleNamespace(generic_text_review_json=handler)
    )
    with pytest.raises(RuntimeError, match="Rust generic-text review failed"):
        rust_core.try_rust_generic_text_review("old", "new", 1)


def test_generic_text_review_accepts_explicit_rust_size_cap_decline(monkeypatch: Any) -> None:
    def handler(*_args: Any) -> str:
        return json.dumps({"used": False, "reason": "input too large for LCS table"})

    monkeypatch.setattr(
        rust_core, "_load_backend", lambda: SimpleNamespace(generic_text_review_json=handler)
    )
    assert rust_core.try_rust_generic_text_review("old", "new", 3) is None


def test_generic_text_review_preserves_valid_empty_rust_result(monkeypatch: Any) -> None:
    def handler(*_args: Any) -> str:
        return json.dumps({"used": True, "changes": [], "group": None})

    monkeypatch.setattr(
        rust_core, "_load_backend", lambda: SimpleNamespace(generic_text_review_json=handler)
    )
    assert rust_core.try_rust_generic_text_review("old", "old", 0) == ([], [])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"used": False},
        {"used": "true"},
        {"used": True},
        {"used": True, "moves": None},
        {"used": True, "moves": [], "renames": [], "moved_labels": [],
         "old_heading_lines": [], "new_heading_lines": [], "move_group": None},
        {"used": True, "moves": [], "renames": [], "moved_labels": [],
         "old_heading_lines": [], "new_heading_lines": [], "move_group": [],
         "rename_group": None},
        {"used": True, "moves": [], "renames": [], "moved_labels": [2],
         "old_heading_lines": [], "new_heading_lines": [], "move_group": None,
         "rename_group": None},
        {"used": True, "moves": [], "renames": [], "moved_labels": [],
         "old_heading_lines": [True], "new_heading_lines": [], "move_group": None,
         "rename_group": None},
    ],
)
def test_markdown_review_invalid_native_envelopes_fail_closed(
    monkeypatch: Any, payload: Any,
) -> None:
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(markdown_section_review_json=lambda *_args: json.dumps(payload)),
    )
    with pytest.raises(RuntimeError, match="Rust markdown section review failed"):
        rust_core.try_rust_markdown_section_review("# Old\\n", "# New\\n")


def test_markdown_review_valid_empty_envelope_is_not_a_decline(
    monkeypatch: Any,
) -> None:
    valid = {
        "used": True,
        "moves": [], "moved_labels": [], "move_group": None,
        "renames": [], "old_heading_lines": [], "new_heading_lines": [],
        "rename_group": None,
    }
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(markdown_section_review_json=lambda *_args: json.dumps(valid)),
    )
    result = rust_core.try_rust_markdown_section_review("# Old\\n", "# Old\\n")
    assert result is not None
    assert result["moves"] == [] and result["renames"] == []
    assert result["old_heading_lines"] == set()
    assert result["new_heading_lines"] == set()


def test_node_facts_enrichment_requires_authoritative_rust_handler(monkeypatch: Any) -> None:
    root = _node("root", "module", "module", line=0)
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="required Rust node facts handler"):
        rust_core.enrich_node_facts(root)


@pytest.mark.parametrize("response", ["{}", "not json", None])
def test_node_facts_enrichment_rejects_malformed_native_success(
    monkeypatch: Any, response: Any,
) -> None:
    root = _node("root", "module", "module", line=0)
    monkeypatch.setattr(
        rust_core,
        "_load_backend",
        lambda: SimpleNamespace(enrich_node_facts_json=lambda *_args: response),
    )
    with pytest.raises(RuntimeError, match="Rust node facts enrichment failed"):
        rust_core.enrich_node_facts(root)


def test_node_facts_enrichment_preserves_valid_noop_and_propagates_native_failure(
    monkeypatch: Any,
) -> None:
    root = _node("root", "module", "module", line=0)
    backend = SimpleNamespace(enrich_node_facts_json=lambda *_args: root.model_dump_json())
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    assert rust_core.enrich_node_facts(root) == root

    def reject(*_args: Any) -> str:
        raise ValueError("native operation failed")

    backend.enrich_node_facts_json = reject
    with pytest.raises(RuntimeError, match="native operation failed"):
        rust_core.enrich_node_facts(root)


def test_profile_enrichment_handler_is_required_not_optional(monkeypatch: Any) -> None:
    root = _node("root", "module", "module", line=0)
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="required Rust profile enrichment handler"):
        rust_core.try_rust_profile_label_enrichment(root, "source", "xml")


def test_profile_enrichment_keeps_valid_noop_and_rejects_malformed_engine_output(
    monkeypatch: Any,
) -> None:
    root = _node("root", "module", "module", line=0)
    backend = SimpleNamespace(enrich_profile_labels_json=lambda *_args: root.model_dump_json())
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    assert rust_core.try_rust_profile_label_enrichment(root, "source", "xml") == root

    backend.enrich_profile_labels_json = lambda *_args: "{}"
    with pytest.raises(RuntimeError, match="Rust profile enrichment failed"):
        rust_core.try_rust_profile_label_enrichment(root, "source", "xml")


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("used", "<missing>"),
        ("used", "true"),
        ("used", 1),
        ("used", None),
        ("changes", "<missing>"),
        ("changes", None),
        ("changes", {}),
        ("change_groups", "<missing>"),
        ("change_groups", None),
        ("ignored_style_changes", "<missing>"),
        ("ignored_style_changes", None),
        ("ignored_style_changes", [None]),
        ("is_style_only", "<missing>"),
        ("is_style_only", "false"),
        ("is_style_only", 0),
        ("no_surviving_changes", "<missing>"),
        ("no_surviving_changes", "true"),
        ("no_surviving_changes", 1),
        ("trace", None),
        ("trace", ["dropped"]),
        ("fallback_diff", []),
    ],
)
def test_rust_finalize_rejects_malformed_success_envelopes(
    monkeypatch: Any, field: str, invalid: Any,
) -> None:
    """Malformed Rust success must never become no-change or style-only evidence."""
    response: dict[str, Any] = {
        "used": True,
        "changes": [],
        "change_groups": [],
        "ignored_style_changes": [],
        "is_style_only": False,
        "no_surviving_changes": True,
    }
    if invalid == "<missing>":
        response.pop(field)
    else:
        response[field] = invalid
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(finalize_review_json=lambda *_: json.dumps(response)),
    )
    root = _node("root", "module", "module", line=0)
    with pytest.raises(RuntimeError, match="invalid Rust finalize response"):
        rust_core.try_rust_finalize_review(
            old_tree=root, new_tree=root, old_source="old", new_source="new",
            language="python", config=DiffConfig(),
        )


def test_rust_finalize_accepts_explicit_empty_arrays_and_false_booleans(
    monkeypatch: Any,
) -> None:
    response = {
        "used": True,
        "changes": [],
        "change_groups": [],
        "ignored_style_changes": [],
        "is_style_only": False,
        "no_surviving_changes": False,
    }
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(finalize_review_json=lambda *_: json.dumps(response)),
    )
    root = _node("root", "module", "module", line=0)
    actual = rust_core.try_rust_finalize_review(
        old_tree=root, new_tree=root, old_source="old", new_source="new",
        language="python", config=DiffConfig(),
    )
    assert actual is not None
    assert actual["changes"] == []
    assert actual["change_groups"] == []
    assert actual["ignored_style_changes"] == []
    assert actual["is_style_only"] is False
    assert actual["no_surviving_changes"] is False
    assert actual["trace"] == []


@pytest.mark.parametrize(
    ("decline", "accepted"),
    [
        ({"used": False, "reason": "tree_too_large"}, True),
        ({"used": False}, False),
        ({"used": False, "reason": "engine_failed"}, False),
    ],
)
def test_rust_finalize_declines_only_for_explicit_tree_limit(
    monkeypatch: Any, decline: dict[str, Any], accepted: bool,
) -> None:
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(finalize_review_json=lambda *_: json.dumps(decline)),
    )
    root = _node("root", "module", "module", line=0)
    def attempt() -> dict[str, Any] | None:
        return rust_core.try_rust_finalize_review(
            old_tree=root, new_tree=root, old_source="old", new_source="new",
            language="python", config=DiffConfig(),
        )
    if accepted:
        assert attempt() is None
    else:
        with pytest.raises(RuntimeError, match="unexpected decline"):
            attempt()


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
        detect_content_type_json=rust_core._load_backend().detect_content_type_json,
        parser_candidate_shortlist=rust_core._load_backend().parser_candidate_shortlist,
        filename_selection_next=rust_core._load_backend().filename_selection_next,
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
        detect_content_type_json=rust_core._load_backend().detect_content_type_json,
        parser_candidate_shortlist=rust_core._load_backend().parser_candidate_shortlist,
        filename_selection_next=rust_core._load_backend().filename_selection_next,
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


def test_rust_core_commit_json_adapter_validates_complete_bytes(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("INTENTUMDIFF_RUST_CORE_VALIDATE_JSON", raising=False)
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
    assert "rust_core_commit_pydantic_validation" in phase_names


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


def test_semantic_differ_batch_decline_requires_native_finalize_handler(monkeypatch: Any) -> None:
    payload = {
        "schema_version": 1,
        "status": "fallback",
        "engine": "rust_core_batch_v4",
        "diffs": [{"status": "fallback", "reason": "changed-file finalization pending"}],
    }
    real_backend = rust_core._load_backend()
    backend = SimpleNamespace(
        detect_content_type_json=rust_core._load_backend().detect_content_type_json,
        parser_candidate_shortlist=rust_core._load_backend().parser_candidate_shortlist,
        filename_selection_next=rust_core._load_backend().filename_selection_next,
        parse_errors_present_json=real_backend.parse_errors_present_json,
        source_fallback_diff_json=real_backend.source_fallback_diff_json,
        # Exercise the real Rust NodeFacts contract; only batch/finalize decline is mocked.
        # An absent enrichment handler must fail closed, not return un-enriched trees.
        enrich_node_facts_json=real_backend.enrich_node_facts_json,
        version=lambda: "0.4.0",
        supports_language=lambda language: language == "python",
        diff_batch=lambda *args: json.dumps(payload),
        apply_invariances_json=_passthrough_invariances_json,
        scope_trails_json=_empty_scope_trails_json,
        enrich_literal_labels_json=lambda tree_json, source: tree_json,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)

    # An absent required Rust finalizer cannot silently become a coarse source diff.
    with pytest.raises(RuntimeError, match="required Rust finalize_review_json handler is unavailable"):
        SemanticDiffer(
            DiffConfig(experimental_rust_core=True, profile_phases=True)
        ).diff_strings(
            "def total(items):\n    return sum(items)\n",
            "def total(items):\n    return sum(item for item in items)\n",
            "example.py",
            language_hint="python",
        )

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


@pytest.mark.parametrize("payload", ["null", '"false"', '"true"', "0", "1", "[]", "{}"])
def test_native_semantic_predicates_reject_nonboolean_success(monkeypatch: Any, payload: str) -> None:
    """Rust semantic flags are bool DTOs, not Python truthiness conversions."""
    backend = SimpleNamespace(
        parse_errors_present_json=lambda *_args: payload,
        review_trees_equivalent_json=lambda *_args: payload,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    root = _node("root", "module", "module", line=0)
    with pytest.raises(ValueError, match="Rust parse_errors_present must return a JSON boolean"):
        rust_core.parse_errors_present("source", "{}", "python")
    with pytest.raises(ValueError, match="Rust review_trees_equivalent must return a JSON boolean"):
        rust_core.review_trees_equivalent(root, root)


@pytest.mark.parametrize("payload,expected", [("true", True), ("false", False)])
def test_native_semantic_predicates_preserve_actual_booleans(
    monkeypatch: Any, payload: str, expected: bool,
) -> None:
    backend = SimpleNamespace(
        parse_errors_present_json=lambda *_args: payload,
        review_trees_equivalent_json=lambda *_args: payload,
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    root = _node("root", "module", "module", line=0)
    assert rust_core.parse_errors_present("source", "{}", "python") is expected
    assert rust_core.review_trees_equivalent(root, root) is expected


@pytest.mark.parametrize("payload", [b"not-json", b"{}", b"null", b"[]"])
def test_native_commit_adapter_rejects_malformed_complete_dto(
    monkeypatch: Any, payload: bytes,
) -> None:
    control = json.dumps({
        "schema_version": 1, "status": "complete",
        "certification": "python_native_v4kb",
        "commit_diff_byte_size": len(payload),
    })
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        supports_language=lambda _language: True,
        diff_batch_commit_json=lambda _request: (control, payload),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    with pytest.raises(RuntimeError, match="malformed CommitDiff JSON"):
        rust_core.try_rust_core_commit_json(
            files=[{
                "old_source": "x = 1\n", "new_source": "x = 2\n",
                "old_filename": "example.py", "new_filename": "example.py",
                "language": "python", "parser_plugin_id": "python",
                "parser_wasm_path": "python_parser.wasm",
            }],
            old_ref="HEAD", new_ref="",
            config=DiffConfig(experimental_rust_core=True),
        )


@pytest.mark.parametrize("payload", [b"not-json", b"{}", b"null", b"[]", b""])
def test_native_working_tree_adapter_rejects_malformed_complete_dto(
    monkeypatch: Any, payload: bytes,
) -> None:
    control = json.dumps({
        "schema_version": 1, "status": "complete",
        "certification": "python_native_v4kb",
        "commit_diff_byte_size": len(payload),
    })
    backend = SimpleNamespace(
        version=lambda: "0.4.0",
        diff_working_tree_python_commit_json=lambda _request: (control, payload),
    )
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    monkeypatch.setattr(rust_core, "rust_core_enabled", lambda _config: True)
    with pytest.raises(RuntimeError, match="malformed CommitDiff JSON"):
        rust_core.try_rust_core_working_tree_commit_json(
            repo_path=".", old_ref="HEAD", config=DiffConfig(experimental_rust_core=True),
        )


def test_scope_trails_accepts_rust_certified_empty_arrays(monkeypatch: Any) -> None:
    """A valid zero-scope response is explicit, never inferred by Python."""
    def scope_trails_json(request_json: str) -> str:
        request = json.loads(request_json)
        assert request["changes"] == []
        return json.dumps({"scope_trails": {"old": [], "new": []}})

    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(scope_trails_json=scope_trails_json),
    )
    tree = SimpleNamespace(model_dump=lambda **_kwargs: {"node_type": "module"})
    assert rust_core.build_scope_trails(
        old_tree=tree, new_tree=tree, changes=[],
    ) == {"old": [], "new": []}


@pytest.mark.parametrize("payload", [
    "{}", "null", "[]", '{"scope_trails": null}',
    '{"scope_trails": {}}',
    '{"scope_trails": {"old": [], "new": null}}',
    '{"scope_trails": {"old": {}, "new": []}}',
    '{"scope_trails": {"old": [42], "new": []}}',
])
def test_scope_trails_rejects_malformed_rust_envelope(
    monkeypatch: Any, payload: str,
) -> None:
    """Never manufacture missing Rust ancestry as an empty success."""
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(scope_trails_json=lambda _request: payload),
    )
    tree = SimpleNamespace(model_dump=lambda **_kwargs: {"node_type": "module"})
    with pytest.raises(RuntimeError, match="Rust scope trail response"):
        rust_core.build_scope_trails(old_tree=tree, new_tree=tree, changes=[])
