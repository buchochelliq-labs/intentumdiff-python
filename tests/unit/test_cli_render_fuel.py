"""#308: the CLI renderer budget follows the API's sentinel-aware policy."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import intentumdiff.plugins.loader as loader
from intentumdiff._differ_runtime import _fuel_budget
from intentumdiff.cli._shared import _render_text
from intentumdiff.core.models import FUEL_UNLIMITED, SemanticDiff

_DIFF = SemanticDiff(old_filename="a.py", new_filename="a.py", language="python")


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


def test_unlimited_sentinel_reaches_the_renderer_unchanged(monkeypatch) -> None:
    assert _rendered_fuel(monkeypatch, FUEL_UNLIMITED) == FUEL_UNLIMITED


@pytest.mark.parametrize("fuel", [None, 500_000, 2_000_000, 50_000_000, 900_000_000])
def test_cli_budget_equals_the_api_budget(monkeypatch, fuel) -> None:
    extra = 20_000_000 + len(_DIFF.model_dump_json()) * 100
    base = 100_000_000 if fuel is None else fuel
    assert _rendered_fuel(monkeypatch, fuel) == _fuel_budget(base, extra)
