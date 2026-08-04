"""Tests for the manual Ataraxy-Labs sem benchmark harness."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts import benchmark_sem


def test_build_scenarios_includes_required_shapes() -> None:
    scenarios = benchmark_sem.build_scenarios()
    ids = {scenario.scenario_id for scenario in scenarios}

    assert {
        "working-tree-small",
        "staged-go-error-wrapping",
        "commit-range-rust-control-flow",
        "working-tree-large-python",
        "rename-move-heavy-python",
        "config-heavy-operational",
        "patch-source-mode-sql",
    } <= ids
    assert any(not scenario.sem_supported for scenario in scenarios)
    assert all(scenario.files for scenario in scenarios)
    assert all(scenario.source_shape for scenario in scenarios)


def test_quick_scenarios_are_small_and_include_sem_unsupported_case() -> None:
    scenarios = benchmark_sem.build_scenarios(quick=True)

    assert [scenario.scenario_id for scenario in scenarios] == [
        "working-tree-small",
        "patch-source-mode-sql",
    ]
    assert scenarios[0].sem_supported is True
    assert scenarios[1].sem_supported is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_prepare_working_tree_scenario_creates_dirty_git_repo(tmp_path) -> None:
    scenario = benchmark_sem.build_scenarios(quick=True)[0]
    prepared = benchmark_sem.prepare_scenario(scenario, tmp_path)

    assert prepared.root.joinpath(".git").exists()
    assert prepared.old_ref == "HEAD"
    assert prepared.new_ref == ""
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=prepared.root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "billing.py" in status


def test_prepare_patch_scenario_creates_patch_artifacts(tmp_path) -> None:
    scenario = benchmark_sem.build_scenarios(quick=True)[1]
    prepared = benchmark_sem.prepare_scenario(scenario, tmp_path)

    assert prepared.patch_file is not None
    assert prepared.patch_base is not None
    assert prepared.patch_file.read_text(encoding="utf-8").startswith("--- a/")
    assert benchmark_sem.build_sem_command(prepared, "sem") is None
    command = benchmark_sem.build_intentumdiff_command(prepared)
    assert command is not None
    assert "patch" in command


def test_change_count_helpers_accept_sem_and_intentumdiff_json_shapes() -> None:
    sem_counts = benchmark_sem._sem_change_counts(
        {
            "summary": {
                "fileCount": 2,
                "added": 1,
                "modified": 2,
                "deleted": 0,
                "total": 3,
            }
        }
    )
    intentumdiff_counts = benchmark_sem._intentumdiff_change_counts(
        [
            {
                "changes": [
                    {"change_type": "ADDITION"},
                    {"change_type": "MODIFICATION"},
                    {"change_type": "MODIFICATION"},
                ]
            }
        ]
    )

    assert sem_counts["total"] == 3
    assert sem_counts["files"] == 2
    assert sem_counts["by_type"]["modified"] == 2
    assert intentumdiff_counts["total"] == 3
    assert intentumdiff_counts["files"] == 1
    assert intentumdiff_counts["by_type"]["MODIFICATION"] == 2


def test_rust_core_discrepancies_record_parity_gap() -> None:
    notes = benchmark_sem._rust_core_discrepancies(
        intentumdiff_status="ok",
        rust_core_status="ok",
        intentumdiff_counts={"total": 3, "files": 1, "by_type": {"MODIFICATION": 3}},
        rust_core_counts={"total": 2, "files": 1, "by_type": {"MODIFICATION": 2}},
        rust_core_notes=[],
    )

    assert notes == ["rust-core parity differs from IntentumDiff: rust-core=2, intentumdiff=3"]


def test_rust_core_discrepancies_record_backend_not_used() -> None:
    notes = benchmark_sem._rust_core_discrepancies(
        intentumdiff_status="ok",
        rust_core_status="ok",
        intentumdiff_counts={"total": 1, "files": 1, "by_type": {"MODIFICATION": 1}},
        rust_core_counts={"total": 1, "files": 1, "by_type": {"MODIFICATION": 1}},
        rust_core_notes=["rust-core lane did not use the Rust backend; check extension install"],
    )

    assert notes == ["rust-core lane did not use the Rust backend"]


def test_rust_core_discrepancies_compare_semantic_signatures() -> None:
    notes = benchmark_sem._rust_core_discrepancies(
        intentumdiff_status="ok",
        rust_core_status="ok",
        intentumdiff_counts={"total": 1, "files": 1, "by_type": {"MODIFICATION": 1}},
        rust_core_counts={"total": 1, "files": 1, "by_type": {"MODIFICATION": 1}},
        rust_core_notes=[],
        intentumdiff_signature=[{"change_type": "MODIFICATION", "old": {"id": "old.a"}}],
        rust_core_signature=[{"change_type": "MODIFICATION", "old": {"id": "old.b"}}],
    )

    assert notes == ["rust-core parity differs from IntentumDiff semantic change signatures"]


def test_signature_parity_report_shows_missing_extra_and_first_mismatch() -> None:
    expected = [
        {"change_type": "ADDITION", "new": {"id": "new.fn"}},
        {"change_type": "MODIFICATION", "old": {"id": "old.if"}},
    ]
    actual = [
        {"change_type": "ADDITION", "new": {"id": "new.block"}},
        {"change_type": "REORDER", "old": {"id": "old.fn"}},
    ]

    report = benchmark_sem._signature_parity_report(
        expected=expected,
        actual=actual,
    )

    assert report["status"] == "mismatch"
    assert report["expected_count"] == 2
    assert report["actual_count"] == 2
    assert report["missing"] == expected
    assert report["extra"] == actual
    assert report["first_mismatch"] == {
        "index": 0,
        "expected": expected[0],
        "actual": actual[0],
    }


def test_signature_parity_report_marks_clean_signatures() -> None:
    signature = [{"change_type": "MODIFICATION", "old": {"id": "old.if"}}]

    report = benchmark_sem._signature_parity_report(
        expected=signature,
        actual=signature.copy(),
    )

    assert report["status"] == "clean"
    assert report["missing"] == []
    assert report["extra"] == []


def test_candidate_batch_helpers_extract_diffs_and_phase_profiles() -> None:
    payload = {
        "diffs": [
            {
                "status": "candidate",
                "old_filename": "example.py",
                "new_filename": "example.py",
                "language": "python",
                "candidate_diff": {
                    "old_filename": "example.py",
                    "new_filename": "example.py",
                    "language": "python",
                    "changes": [{"change_type": "MODIFICATION"}],
                },
                "phase_timings": [
                    {"name": "rust_matching", "duration_ms": 7.0},
                ],
            }
        ]
    }

    diffs = benchmark_sem._candidate_diffs_from_batch(payload)
    profiles = benchmark_sem._candidate_phase_profiles_from_batch(
        payload,
        category="cold",
        sample_index=0,
        prefix="rust_native_candidate",
    )

    assert diffs[0]["changes"][0]["change_type"] == "MODIFICATION"
    assert profiles[0]["phase_timings"]["phases"][0]["name"] == (
        "rust_native_candidate.rust_matching"
    )


def test_validate_report_accepts_rust_candidate_status() -> None:
    report = {
        "scenarios": [
            {
                "scenario_id": "working-tree-small",
                "language": "python",
                "tier": "simple",
                "source_shape": "shape",
                "sem_status": "ok",
                "intentumdiff_status": "ok",
                "intentumdiff_rust_batch_status": "ok",
                "intentumdiff_rust_batch_parity": "clean",
                "intentumdiff_rust_batch_parity_details": {
                    "status": "clean",
                    "missing": [],
                    "extra": [],
                    "first_mismatch": None,
                    "expected_count": 1,
                    "actual_count": 1,
                },
                "intentumdiff_rust_batch_parallel_status": "ok",
                "intentumdiff_rust_batch_parallel_parity": "clean",
                "intentumdiff_rust_batch_parallel_parity_details": {
                    "status": "clean",
                    "missing": [],
                    "extra": [],
                    "first_mismatch": None,
                    "expected_count": 1,
                    "actual_count": 1,
                },
                "intentumdiff_rust_candidate_status": "ok",
                "intentumdiff_rust_candidate_parity": "clean",
                "intentumdiff_rust_candidate_parity_details": {
                    "status": "clean",
                    "missing": [],
                    "extra": [],
                    "first_mismatch": None,
                    "expected_count": 1,
                    "actual_count": 1,
                },
                "intentumdiff_rust_native_candidate_status": "ok",
                "intentumdiff_rust_native_candidate_parity": "clean",
                "intentumdiff_rust_native_candidate_parity_details": {
                    "status": "clean",
                    "missing": [],
                    "extra": [],
                    "first_mismatch": None,
                    "expected_count": 1,
                    "actual_count": 1,
                },
                "sem_command": ["sem"],
                "intentumdiff_command": ["intentumdiff"],
                "timings": {
                    "sem": {},
                    "intentumdiff": {},
                    "intentumdiff_rust_batch": {},
                    "intentumdiff_rust_batch_parallel": {},
                    "intentumdiff_rust_candidate": {},
                    "intentumdiff_rust_native_candidate": {},
                },
                "change_counts": {
                    "sem": {},
                    "intentumdiff": {},
                    "intentumdiff_rust_batch": {},
                    "intentumdiff_rust_batch_parallel": {},
                    "intentumdiff_rust_candidate": {},
                    "intentumdiff_rust_native_candidate": {},
                },
                "json_parse": {
                    "sem": "ok",
                    "intentumdiff": "ok",
                    "intentumdiff_rust_batch": "ok",
                    "intentumdiff_rust_batch_parallel": "ok",
                    "intentumdiff_rust_candidate": "ok",
                    "intentumdiff_rust_native_candidate": "ok",
                },
                "phase_summary": {
                    "intentumdiff": {},
                    "intentumdiff_rust_batch": {},
                    "intentumdiff_rust_batch_parallel": {},
                    "intentumdiff_rust_candidate": {},
                    "intentumdiff_rust_native_candidate": {},
                },
                "discrepancies": [],
                "notes": [],
            }
        ],
        "language_issue_log": [
            {
                "language": "python",
                "scenario_id": "working-tree-small",
                "issue_type": "none",
                "severity": "none",
                "observed_behavior": "No issue recorded.",
                "expected_or_desired_behavior": "Use benchmark evidence.",
                "suggested_fix": "None.",
                "labels": ["test-candidate"],
            }
        ],
    }

    benchmark_sem.validate_report(report)


def test_render_summary_includes_rust_core_sequential_lane() -> None:
    report = {
        "metadata": {
            "generated_at": "2026-05-28T00:00:00+00:00",
            "environment": "test",
            "sem_version": "sem test",
        },
        "scenarios": [
            {
                "scenario_id": "working-tree-large-python",
                "sem_status": "ok",
                "intentumdiff_status": "ok",
                "timings": {
                    "sem": {"steady_state_warm_mean_ms": 10.0},
                    "intentumdiff": {
                        "first_warm_mean_ms": 30.0,
                        "steady_state_warm_mean_ms": 20.0,
                    },
                    "intentumdiff_rust_core": {"steady_state_warm_mean_ms": 12.0},
                    "intentumdiff_rust_core_sequential": {
                        "steady_state_warm_mean_ms": 18.0
                    },
                },
                "phase_summary": {
                    "intentumdiff": {},
                    "intentumdiff_rust_core": {},
                    "intentumdiff_rust_core_sequential": {},
                },
                "discrepancies": [],
                "notes": [],
            }
        ],
    }

    summary = benchmark_sem.render_summary(report)

    assert "intentumdiff rust seq steady warm" in summary
    assert "18.0 ms" in summary


def test_timing_summary_separates_first_and_steady_warm_samples() -> None:
    measurement = benchmark_sem.CommandMeasurement(
        status="ok",
        command=["tool"],
        cwd=".",
        cold_ms=[100.0],
        warm_ms=[90.0, 20.0, 30.0],
        stdout="{}",
        stderr="",
        returncode=0,
        json_payload={},
        json_parse="ok",
        phase_profiles=[],
        notes=[],
    )

    summary = benchmark_sem._timing_summary(measurement)

    assert summary["first_warm_ms"] == [90.0]
    assert summary["steady_state_warm_ms"] == [20.0, 30.0]
    assert summary["first_warm_mean_ms"] == 90.0
    assert summary["steady_state_warm_mean_ms"] == 25.0


def test_phase_summary_aggregates_profile_samples() -> None:
    measurement = benchmark_sem.CommandMeasurement(
        status="ok",
        command=["intentumdiff-api"],
        cwd=".",
        cold_ms=[100.0],
        warm_ms=[90.0, 20.0],
        stdout="[]",
        stderr="",
        returncode=0,
        json_payload=[],
        json_parse="ok",
        phase_profiles=[
            {
                "category": "cold",
                "sample_index": 0,
                "phase_timings": {
                    "phases": [
                        {"name": "parser_selection", "duration_ms": 10.0},
                        {"name": "wasm_plugin_execution", "duration_ms": 30.0},
                    ]
                },
            },
            {
                "category": "steady_state",
                "sample_index": 2,
                "phase_timings": {
                    "phases": [
                        {"name": "parser_selection", "duration_ms": 2.0},
                        {"name": "matching", "duration_ms": 8.0},
                    ]
                },
            },
        ],
        notes=[],
    )

    summary = benchmark_sem._phase_summary(measurement)

    assert summary["available"] is True
    assert summary["profile_count"] == 2
    assert summary["top_phases"][0] == {
        "name": "wasm_plugin_execution",
        "duration_ms": 30.0,
    }
    assert summary["by_category"]["cold"]["parser_selection"] == 10.0
    assert summary["by_category"]["steady_state"]["matching"] == 8.0


def test_phase_summary_counts_shared_phase_once_per_sample() -> None:
    measurement = benchmark_sem.CommandMeasurement(
        status="ok",
        command=["intentumdiff-api"],
        cwd=".",
        cold_ms=[100.0],
        warm_ms=[],
        stdout="[]",
        stderr="",
        returncode=0,
        json_payload=[],
        json_parse="ok",
        phase_profiles=[
            {
                "category": "cold",
                "sample_index": 0,
                "phase_timings": {
                    "phases": [
                        {
                            "name": "source_collection",
                            "duration_ms": 25.0,
                            "shared": True,
                        },
                        {"name": "matching", "duration_ms": 4.0},
                    ]
                },
            },
            {
                "category": "cold",
                "sample_index": 0,
                "phase_timings": {
                    "phases": [
                        {
                            "name": "source_collection",
                            "duration_ms": 25.0,
                            "shared": True,
                        },
                        {"name": "matching", "duration_ms": 6.0},
                    ]
                },
            },
        ],
        notes=[],
    )

    summary = benchmark_sem._phase_summary(measurement)

    assert summary["by_category"]["cold"]["source_collection"] == 25.0
    assert summary["by_category"]["cold"]["matching"] == 10.0


def test_phase_summary_excludes_broad_adapter_totals() -> None:
    measurement = benchmark_sem.CommandMeasurement(
        status="ok",
        command=["intentumdiff-api-rust-core"],
        cwd=".",
        cold_ms=[100.0],
        warm_ms=[],
        stdout="[]",
        stderr="",
        returncode=0,
        json_payload=[],
        json_parse="ok",
        phase_profiles=[
            {
                "category": "cold",
                "sample_index": 0,
                "phase_timings": {
                    "phases": [
                        {
                            "name": "rust_core_commit_pyo3_call",
                            "duration_ms": 100.0,
                            "shared": True,
                            "summary_exclude": True,
                        },
                        {
                            "name": "rust_batch.rust_batch_file_execution",
                            "duration_ms": 25.0,
                            "shared": True,
                        },
                    ]
                },
            },
        ],
        notes=[],
    )

    summary = benchmark_sem._phase_summary(measurement)

    assert summary["top_phases"] == [
        {"name": "rust_batch.rust_batch_file_execution", "duration_ms": 25.0}
    ]
    assert "rust_core_commit_pyo3_call" not in summary["by_category"]["cold"]


def test_phase_profiles_include_rust_core_internal_timings() -> None:
    profiles = benchmark_sem._phase_profiles_from_diff_payload(
        {
            "old_filename": "old.py",
            "new_filename": "new.py",
            "language": "python",
            "metadata": {
                "phase_timings": {
                    "phases": [{"name": "parser_selection", "duration_ms": 3.0}]
                },
                "rust_phase_timings": [
                    {"name": "rust_tree_sitter_parse_old", "duration_ms": 5.0}
                ],
            },
        },
        category="cold",
        sample_index=0,
    )

    assert len(profiles) == 2
    rust_phases = profiles[1]["phase_timings"]["phases"]
    assert rust_phases == [
        {"name": "rust.rust_tree_sitter_parse_old", "duration_ms": 5.0}
    ]


def test_phase_profiles_include_rust_timings_without_python_phase_block() -> None:
    profiles = benchmark_sem._phase_profiles_from_diff_payload(
        {
            "old_filename": "old.py",
            "new_filename": "new.py",
            "language": "python",
            "metadata": {
                "rust_phase_timings": [
                    {"name": "rust_wasm_process_old", "duration_ms": 7.0}
                ],
            },
        },
        category="steady_state",
        sample_index=2,
    )

    assert len(profiles) == 1
    assert profiles[0]["phase_timings"]["phases"] == [
        {"name": "rust.rust_wasm_process_old", "duration_ms": 7.0}
    ]


def test_phase_profiles_include_shared_rust_batch_preload_phase() -> None:
    profiles = benchmark_sem._phase_profiles_from_diff_payload(
        {
            "old_filename": "old.py",
            "new_filename": "new.py",
            "language": "python",
            "metadata": {
                "rust_core_batch": {
                    "phase_timings": [
                        {
                            "name": "rust_wasm_batch_component_preload",
                            "duration_ms": 4.0,
                        }
                    ]
                }
            },
        },
        category="steady_state",
        sample_index=2,
    )

    assert len(profiles) == 1
    assert profiles[0]["phase_timings"]["phases"] == [
        {
            "name": "rust_batch.rust_wasm_batch_component_preload",
            "duration_ms": 4.0,
            "shared": True,
        }
    ]


def test_adapter_phase_profiles_are_shared_batch_phases() -> None:
    profiles = benchmark_sem._adapter_phase_profiles(
        [
            {"name": "rust_core_request_json_encode", "duration_ms": 1.0},
            {"name": "rust_core_batch_execution", "duration_ms": 9.0},
        ],
        category="steady_state",
        sample_index=3,
        label="intentumdiff-api-rust-batch",
    )

    assert profiles == [
        {
            "category": "steady_state",
            "sample_index": 3,
            "old_filename": "<batch>",
            "new_filename": "intentumdiff-api-rust-batch",
            "language": "python",
            "phase_timings": {
                "schema_version": 1,
                "phases": [
                    {
                        "name": "rust_core_request_json_encode",
                        "duration_ms": 1.0,
                        "shared": True,
                    },
                    {
                        "name": "rust_core_batch_execution",
                        "duration_ms": 9.0,
                        "shared": True,
                    },
                ],
            },
        }
    ]


def test_certified_commit_json_phase_profiles_include_control_and_batch_phases() -> None:
    attempt = SimpleNamespace(
        adapter_phase_timings=[
            {"name": "rust_core_commit_request_json_encode", "duration_ms": 1.0}
        ],
        control={
            "phase_timings": [
                {"name": "rust_commit_json_output_validation", "duration_ms": 2.0}
            ],
            "batch_metadata": {
                "phase_timings": [
                    {"name": "rust_batch_request_decode", "duration_ms": 3.0}
                ]
            },
        },
    )

    profiles = benchmark_sem._certified_commit_json_phase_profiles(
        attempt,
        category="steady_state",
        sample_index=4,
        label="intentumdiff-api-rust-core",
    )

    assert [profile["old_filename"] for profile in profiles] == [
        "<batch>",
        "<commit-json>",
        "<commit-json>",
    ]
    assert profiles[0]["phase_timings"]["phases"][0]["name"] == (
        "rust_core_commit_request_json_encode"
    )
    assert profiles[1]["phase_timings"]["phases"][0]["name"] == (
        "rust_commit_json.rust_commit_json_output_validation"
    )
    assert profiles[2]["phase_timings"]["phases"][0]["name"] == (
        "rust_batch.rust_batch_request_decode"
    )
    assert all(
        profile["phase_timings"]["phases"][0].get("shared") is True
        for profile in profiles
    )


def test_entity_fast_path_summary_aggregates_rust_metadata() -> None:
    summary = benchmark_sem._entity_fast_path_summary(
        [
            {
                "metadata": {
                    "rust_core": {
                        "details": {
                            "entity_fast_path": {
                                "attempted": True,
                                "used": True,
                                "disabled": False,
                                "old_entities": 2,
                                "new_entities": 3,
                                "seeded_matches": 1,
                                "descendant_seeded_matches": 4,
                                "fuzzy_token_candidates": 2,
                                "matches_by_strategy": {
                                    "exact_id": 1,
                                    "structural": 0,
                                    "label_parent": 0,
                                },
                                "edit_script": {
                                    "delete_candidates": 2,
                                    "add_candidates": 1,
                                    "pruned_old_descendant_deletes": 4,
                                    "pruned_new_descendant_additions": 3,
                                },
                                "refinement": {
                                    "initial_change_count": 5,
                                    "final_change_count": 3,
                                    "suppressed_add_delete_noise": 2,
                                },
                            }
                        }
                    }
                }
            },
            {
                "metadata": {
                    "rust_core": {
                        "details": {
                            "entity_fast_path": {
                                "attempted": True,
                                "used": False,
                                "disabled": True,
                                "disabled_reason": "parity guard",
                                "old_entities": 1,
                                "new_entities": 1,
                                "seeded_matches": 0,
                                "descendant_seeded_matches": 0,
                                "fuzzy_token_candidates": 1,
                                "matches_by_strategy": {"exact_id": 0},
                                "edit_script": {"delete_candidates": 1},
                                "refinement": {"initial_change_count": 1},
                            }
                        }
                    }
                }
            },
        ]
    )

    assert summary["available"] is True
    assert summary["file_count"] == 2
    assert summary["attempted_files"] == 2
    assert summary["used_files"] == 1
    assert summary["disabled_files"] == 1
    assert summary["old_entities"] == 3
    assert summary["new_entities"] == 4
    assert summary["seeded_matches"] == 1
    assert summary["descendant_seeded_matches"] == 4
    assert summary["fuzzy_token_candidates"] == 3
    assert summary["matches_by_strategy"]["exact_id"] == 1
    assert summary["edit_script"]["delete_candidates"] == 3
    assert summary["edit_script"]["pruned_old_descendant_deletes"] == 4
    assert summary["refinement"]["initial_change_count"] == 6
    assert summary["disabled_reasons"] == ["parity guard"]


def test_report_validation_requires_mirrored_issue_log() -> None:
    report = {
        "metadata": {"schema_version": 1},
        "scenarios": [
            {
                "scenario_id": "s1",
                "language": "python",
                "tier": "simple",
                "source_shape": "shape",
                "sem_status": "ok",
                "intentumdiff_status": "ok",
                "sem_command": ["sem", "diff"],
                "intentumdiff_command": ["intentumdiff-api"],
                "timings": {},
                "change_counts": {},
                "json_parse": {},
                "phase_summary": {"intentumdiff": {"available": False}},
                "discrepancies": [],
                "notes": [],
            }
        ],
        "language_issue_log": [
            {
                "language": "python",
                "scenario_id": "s1",
                "issue_type": "none",
                "severity": "none",
                "observed_behavior": "No issue recorded.",
                "expected_or_desired_behavior": "Use benchmark evidence.",
                "suggested_fix": "No fix.",
                "labels": ["test-candidate"],
            }
        ],
    }

    benchmark_sem.validate_report(report)
    report["scenarios"][0]["intentumdiff_parallel_status"] = "ok"
    report["scenarios"][0]["intentumdiff_parallel_command"] = ["intentumdiff-api-parallel"]
    report["scenarios"][0]["timings"]["intentumdiff_parallel"] = {}
    report["scenarios"][0]["change_counts"]["intentumdiff_parallel"] = {}
    report["scenarios"][0]["json_parse"]["intentumdiff_parallel"] = "ok"
    report["scenarios"][0]["phase_summary"]["intentumdiff_parallel"] = {"available": False}
    benchmark_sem.validate_report(report)
    report["scenarios"][0]["intentumdiff_parallel_status"] = "bogus"
    with pytest.raises(ValueError, match="parallel status"):
        benchmark_sem.validate_report(report)
    report["scenarios"][0]["intentumdiff_parallel_status"] = "ok"
    report["language_issue_log"] = []
    with pytest.raises(ValueError, match="language issue log"):
        benchmark_sem.validate_report(report)


def test_missing_sem_returns_install_guidance(tmp_path, capsys) -> None:
    code = benchmark_sem.run_benchmark(
        sem="definitely-missing-sem-binary",
        out=tmp_path / "out",
        quick=True,
        cold=1,
        warm=1,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "Required sem binary" in captured.err
    assert not (tmp_path / "out" / "report.json").exists()
