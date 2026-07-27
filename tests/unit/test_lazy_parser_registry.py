"""Unit tests for lazy parser catalog loading."""

from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from intentdiff.core.models import DiffConfig
from intentdiff.plugins.exceptions import PluginNotFoundError
from intentdiff.plugins.registry import PluginRegistry, _wasm_path_from_ep


class _FakeDist:
    def __init__(
        self,
        name: str = "intentdiff",
        *,
        metadata: dict[str, str] | None = None,
        files: list[PurePosixPath] | None = None,
        root: object | None = None,
    ) -> None:
        self.name = name
        self.metadata = metadata or {"Name": name, "Version": "1.0.0", "Author": "Tests"}
        self.files = files
        self._root = root

    def locate_file(self, file: PurePosixPath) -> object:
        if self._root is None:
            return file
        return self._root / str(file)


def _entry(name: str, *, dist_name: str = "intentdiff") -> SimpleNamespace:
    return SimpleNamespace(name=name, dist=_FakeDist(dist_name))


def _plugin(grammar_id: str, language_ids: list[str], detects_as: str) -> MagicMock:
    plugin = MagicMock()
    plugin.trusted = True
    plugin.wasm_path = f"{grammar_id}.wasm"
    plugin.call_grammar_id.return_value = grammar_id
    plugin.call_language_ids.return_value = language_ids
    plugin.call_priority.return_value = 0
    plugin.call_detect_language.return_value = detects_as
    plugin.call_parser_mode.return_value = "full-parse"
    plugin.call_trivia_node_types.return_value = []
    return plugin


def _wasm_path(ep: SimpleNamespace) -> str:
    if ep.name in {"c", "cpp"}:
        return "C:/tmp/cpp_parser.wasm"
    return f"C:/tmp/{ep.name}_parser.wasm"


def test_third_party_wasm_path_uses_intentdiff_metadata_field(tmp_path) -> None:
    wasm_rel = PurePosixPath("pkg/wasm/my_parser.wasm")
    wasm_path = tmp_path / str(wasm_rel)
    wasm_path.parent.mkdir(parents=True)
    wasm_path.write_bytes(b"\0asm")
    dist = _FakeDist(
        "intentdiff-third-party",
        metadata={
            "Name": "intentdiff-third-party",
            "Version": "1.0.0",
            "Author": "Tests",
            "IntentDiff-Wasm-Path": str(wasm_rel),
        },
        files=[wasm_rel],
        root=tmp_path,
    )
    ep = SimpleNamespace(name="third-party", dist=dist)

    assert _wasm_path_from_ep(ep) == str(wasm_path)


def test_third_party_legacy_pysd_wasm_path_field_is_rejected(tmp_path) -> None:
    legacy_field = "Py" + "sd" + "-Wasm-Path"
    dist = _FakeDist(
        "intentdiff-third-party",
        metadata={
            "Name": "intentdiff-third-party",
            "Version": "1.0.0",
            "Author": "Tests",
            legacy_field: "pkg/wasm/my_parser.wasm",
        },
        files=[PurePosixPath("pkg/wasm/my_parser.wasm")],
        root=tmp_path,
    )
    ep = SimpleNamespace(name="third-party", dist=dist)

    with pytest.raises(ValueError, match="IntentDiff-Wasm-Path"):
        _wasm_path_from_ep(ep)


def test_catalog_discovery_does_not_instantiate_plugins() -> None:
    registry = PluginRegistry(DiffConfig())

    with (
        patch(
            "intentdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("python"), _entry("sql")],
        ),
        patch("intentdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch("intentdiff.plugins.registry.load_plugin") as load_plugin,
    ):
        catalog = registry._catalog()

    assert [entry.entry_names for entry in catalog] == [["python"], ["sql"]]
    load_plugin.assert_not_called()


def test_filename_selection_loads_only_matching_candidate() -> None:
    registry = PluginRegistry(DiffConfig())

    def load(path: str, *_args: object, **_kwargs: object) -> MagicMock:
        if "python" in path:
            return _plugin("python", ["python"], "python")
        return _plugin("sql", ["sql"], "")

    with (
        patch(
            "intentdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("python"), _entry("sql")],
        ),
        patch("intentdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch("intentdiff.plugins.registry.load_plugin", side_effect=load) as load_plugin,
    ):
        phases: list[str] = []
        parser, language = registry.detect_parser(
            "example.py",
            "def f(): pass",
            phase_recorder=lambda name, _duration: phases.append(name),
        )

    assert parser.grammar_id == "python"
    assert language == "python"
    assert load_plugin.call_count == 1
    assert "python_parser.wasm" in load_plugin.call_args.args[0]
    assert "parser_entrypoint_discovery" in phases
    assert "parser_candidate_shortlist" in phases
    assert "parser_plugin_instantiation" in phases
    assert "parser_plugin_language_detection" in phases


def test_duplicate_entry_points_sharing_wasm_load_once() -> None:
    registry = PluginRegistry(DiffConfig())

    with (
        patch(
            "intentdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("c"), _entry("cpp")],
        ),
        patch("intentdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch(
            "intentdiff.plugins.registry.load_plugin",
            return_value=_plugin("cpp", ["c", "cpp"], "cpp"),
        ) as load_plugin,
    ):
        parser, language = registry.detect_parser("code.cpp", "int main() {}")

    assert parser.grammar_id == "cpp"
    assert language == "cpp"
    assert load_plugin.call_count == 1
    assert registry._catalog()[0].entry_names == ["c", "cpp"]


def test_disabled_first_party_entry_point_is_not_cataloged() -> None:
    registry = PluginRegistry(DiffConfig())

    with (
        patch(
            "intentdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("freebasic")],
        ),
        patch("intentdiff.plugins.registry._wasm_path_from_ep") as wasm_path,
    ):
        assert registry._catalog() == []

    wasm_path.assert_not_called()


def test_relevant_parser_load_failure_is_reported() -> None:
    registry = PluginRegistry(DiffConfig())

    with (
        patch(
            "intentdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("python")],
        ),
        patch("intentdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch("intentdiff.plugins.registry.load_plugin", side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(PluginNotFoundError):
            registry.detect_parser("example.py", "def f(): pass")

    summary = registry.parser_load_failure_summary()
    assert summary is not None
    assert "boom" in summary
