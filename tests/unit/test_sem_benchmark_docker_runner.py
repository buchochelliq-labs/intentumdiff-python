"""Tests for the Docker/Linux sem benchmark runner."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import benchmark_sem_docker


def _args(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "docker": "docker",
        "image": "python:3.12-bookworm",
        "pull": "missing",
        "out": Path("artifacts/sem-benchmark"),
        "quick_only": False,
        "skip_quick": False,
        "cold": 3,
        "warm": 5,
        "timeout": 60.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_docker_command_mounts_repo_and_uses_named_caches() -> None:
    command = benchmark_sem_docker.build_docker_command(_args())

    assert command[:5] == ["docker", "run", "--rm", "--pull", "missing"]
    assert f"{benchmark_sem_docker.REPO_ROOT}:/workspace" in command
    assert "intentumdiff-sem-cargo-cache:/root/.cargo" in command
    assert "intentumdiff-sem-rustup-cache:/root/.rustup" in command
    assert "intentumdiff-sem-pip-cache:/root/.cache/pip" in command


def test_container_script_runs_quick_before_full_by_default() -> None:
    script = benchmark_sem_docker._container_script(_args())

    quick_index = script.index("--environment docker-linux --quick")
    full_index = script.rindex("--environment docker-linux")
    assert quick_index < full_index
    assert "cargo install --git https://github.com/Ataraxy-Labs/sem sem-cli --bin sem" in script


def test_container_script_can_run_quick_only() -> None:
    script = benchmark_sem_docker._container_script(_args(quick_only=True))

    assert script.count("python scripts/benchmark_sem.py") == 1
    assert "--quick" in script
