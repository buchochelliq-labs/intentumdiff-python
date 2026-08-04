"""Project configuration loading for ``intentumdiff.yaml`` (Rust-authoritative, #99).

The file walk, YAML parse, and ``config``-section validation run in the Rust core
(``config.rs`` via ``load_project_diff_config_json`` / ``find_intentumdiff_config_path``).
Python keeps only the public API surface and the ``DiffConfig`` DTO construction, so
every binding resolves project config identically.
"""

from __future__ import annotations

from pathlib import Path

from intentumdiff.core.models import DiffConfig

INTENTUMDIFF_CONFIG_FILENAME = "intentumdiff.yaml"


def load_project_diff_config(
    start_path: str | Path | None = None,
    *,
    explicit_path: str | Path | None = None,
) -> DiffConfig:
    """Load ``DiffConfig`` defaults from the nearest ``intentumdiff.yaml``.

    The project file uses a top-level ``config`` mapping for diff tuning and a
    separate ``guardrails`` mapping for protected semantic paths. Missing files or
    missing ``config`` sections return ``DiffConfig()``; an unsupported ``config``
    key raises ``ValueError`` (from the Rust core).
    """
    from intentumdiff.rust_core import load_project_diff_config_data

    data = load_project_diff_config_data(
        None if start_path is None else str(start_path),
        None if explicit_path is None else str(explicit_path),
    )
    return DiffConfig(**data)


def find_intentumdiff_config(
    start_path: str | Path | None = None,
    *,
    explicit_path: str | Path | None = None,
) -> Path | None:
    """Return the nearest ``intentumdiff.yaml`` from *start_path* upward (Rust core)."""
    from intentumdiff.rust_core import resolve_intentumdiff_config_path

    resolved = resolve_intentumdiff_config_path(
        None if start_path is None else str(start_path),
        None if explicit_path is None else str(explicit_path),
    )
    return Path(resolved) if resolved is not None else None
