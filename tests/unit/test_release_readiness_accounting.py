from __future__ import annotations

from scripts import release_readiness_accounting as accounting


def test_release_readiness_accounting_tracks_expected_gate_ids() -> None:
    gate_ids = set(accounting.gate_ids())

    assert {
        "python-competitor-matrix",
        "python-fuel-truth",
        "supported-language-examples",
        "wasm-build",
        "rust-priority-parsers",
        "rust-asset-diff",
        "cpp-depth-proof",
        "vscode-unit-suite",
        "electron-review-shell",
        "release-media-manifest",
        "release-media-manifest-powershell",
        "python-native-wheel-dry-run",
        "python-native-wheel-verify",
        "vscode-vsix-dry-run",
        "visualstudio-scaffold-build",
        "git-diff-check",
    } <= gate_ids


def test_release_readiness_accounting_keeps_packaging_dry_runs_environment_gated() -> None:
    gates = {gate.id: gate for gate in accounting.GATES}

    assert gates["vscode-vsix-dry-run"].status == "environment"
    assert gates["release-media-manifest-powershell"].status == "environment"
    assert gates["python-native-wheel-dry-run"].status == "environment"
    assert gates["python-native-wheel-verify"].status == "environment"
    assert gates["visualstudio-scaffold-build"].status == "required"
    assert gates["vscode-vsix-dry-run"].priority == "P1"
    assert gates["python-native-wheel-dry-run"].priority == "P1"
    assert gates["visualstudio-scaffold-build"].priority == "P1"


def test_release_readiness_accounting_payload_is_json_ready() -> None:
    payload = accounting.accounting_payload("P0")

    assert payload["gate_count"] >= 7
    assert payload["required_count"] == payload["gate_count"]
    assert payload["environment_count"] == 0
    for gate in payload["gates"]:
        assert isinstance(gate["command"], list)
        assert gate["cwd"]
        assert gate["proves"]
