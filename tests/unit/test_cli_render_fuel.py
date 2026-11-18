"""#308 / S10: every Wasm fuel budget is core's ``fuel_budget`` answer, transported unchanged.

Core's policy (``max(plugin_fuel, 20M + 20_000 * content_len)``, a budget below 1M kept
exactly, a negative ``plugin_fuel`` unlimited) lives only in core. The binding forwards
``[{"plugin_fuel": ...}, content_len]`` and maps core's ``null`` to ``FUEL_UNLIMITED``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import intentumdiff.plugins.loader as loader
from intentumdiff import rust_core
from intentumdiff.cli._shared import _render_text
from intentumdiff.core.models import FUEL_UNLIMITED, DiffConfig, SemanticDiff
from intentumdiff.differ import SemanticDiffer

_DIFF = SemanticDiff(old_filename="a.py", new_filename="a.py", language="python")


def _canned(monkeypatch: pytest.MonkeyPatch, result: object) -> list[tuple]:
    calls: list[tuple] = []

    def fuel_budget(*args):
        calls.append(args)
        return json.dumps(result)

    backend = SimpleNamespace(fuel_budget=fuel_budget)
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    return calls


def _rendered_fuel(monkeypatch: pytest.MonkeyPatch, fuel: int | None) -> int:
    seen: list[int] = []

    def fake_load_plugin(_path, plugin_fuel=None):
        seen.append(plugin_fuel)
        renderer = MagicMock()
        renderer.call_render.return_value = "rendered"
        return renderer

    monkeypatch.setattr(loader, "load_plugin", fake_load_plugin)
    assert _render_text(_DIFF, "patch", fuel=fuel) == "rendered"
    return seen[-1]


# ── passthrough: one core op, its answer unchanged ──────────────────────────


@pytest.mark.parametrize(
    ("canned", "expected"), [(123_456_789, 123_456_789), (None, FUEL_UNLIMITED)]
)
def test_the_cli_renderer_gets_cores_budget_unchanged(monkeypatch, canned, expected) -> None:
    calls = _canned(monkeypatch, {"fuel": canned})
    assert _rendered_fuel(monkeypatch, 7) == expected
    assert calls == [(json.dumps({"plugin_fuel": 7}), len(_DIFF.model_dump_json().encode()))]


def test_the_api_renderer_gets_cores_budget_unchanged(monkeypatch) -> None:
    calls = _canned(monkeypatch, {"fuel": 424_242})
    differ = SemanticDiffer(DiffConfig(plugin_fuel=5_000_000))
    renderer = MagicMock()
    renderer.render.return_value = "out"
    monkeypatch.setattr(differ._registry, "get_renderer", lambda _fmt: renderer)
    assert differ.render(_DIFF, "patch") == "out"
    assert renderer.render.call_args.kwargs["fuel"] == 424_242
    content_len = len(_DIFF.model_dump_json().encode())
    assert calls == [(json.dumps({"plugin_fuel": 5_000_000}), content_len)]


@pytest.mark.parametrize(
    "result", [{}, {"fuel": -5}, {"fuel": "1"}, {"fuel": True}, {"fuel": 1.5}, []]
)
def test_a_malformed_core_answer_raises(monkeypatch, result) -> None:
    _canned(monkeypatch, result)
    with pytest.raises(RuntimeError, match="fuel_budget"):
        rust_core.fuel_budget(100_000_000, 10)


def test_a_core_rejection_raises(monkeypatch) -> None:
    def reject(*_args):
        raise ValueError("argument 1 (content_len) must be a non-negative integer")

    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(fuel_budget=reject))
    with pytest.raises(RuntimeError, match="content_len"):
        rust_core.fuel_budget(100_000_000, -1)


# ── real core: the policy is core's, including the 20_000-per-byte scale ────


@pytest.mark.parametrize(
    ("plugin_fuel", "content_len", "expected"),
    [
        (10_000_000, 0, 20_000_000),
        (10_000_000, 1_000, 40_000_000),
        (500_000_000, 1_000, 500_000_000),
        (999_999, 1_000_000, 999_999),
        (FUEL_UNLIMITED, 1_000, FUEL_UNLIMITED),
    ],
)
def test_real_core_policy(plugin_fuel, content_len, expected) -> None:
    assert rust_core.fuel_budget(plugin_fuel, content_len) == expected


def test_real_core_rejects_a_negative_length() -> None:
    with pytest.raises(RuntimeError, match="content_len"):
        rust_core.fuel_budget(100_000_000, -1)


def test_unlimited_sentinel_reaches_the_renderer_unchanged(monkeypatch) -> None:
    assert _rendered_fuel(monkeypatch, FUEL_UNLIMITED) == FUEL_UNLIMITED


@pytest.mark.parametrize("fuel", [None, 500_000, 2_000_000, 50_000_000, 900_000_000])
def test_cli_budget_equals_cores_budget(monkeypatch, fuel) -> None:
    base = 100_000_000 if fuel is None else fuel
    expected = rust_core.fuel_budget(base, len(_DIFF.model_dump_json().encode()))
    assert _rendered_fuel(monkeypatch, fuel) == expected
