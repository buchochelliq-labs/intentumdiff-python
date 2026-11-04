"""Unit tests for lazy parser catalog loading."""

from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from intentumdiff.core.models import DiffConfig
from intentumdiff.plugins.exceptions import PluginNotFoundError
from intentumdiff.plugins.registry import PluginRegistry, _wasm_path_from_ep


class _FakeDist:
    def __init__(
        self,
        name: str = "intentumdiff",
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


def _entry(name: str, *, dist_name: str = "intentumdiff") -> SimpleNamespace:
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


def test_third_party_wasm_path_uses_intentumdiff_metadata_field(tmp_path) -> None:
    wasm_rel = PurePosixPath("pkg/wasm/my_parser.wasm")
    wasm_path = tmp_path / str(wasm_rel)
    wasm_path.parent.mkdir(parents=True)
    wasm_path.write_bytes(b"\0asm")
    dist = _FakeDist(
        "intentumdiff-third-party",
        metadata={
            "Name": "intentumdiff-third-party",
            "Version": "1.0.0",
            "Author": "Tests",
            "IntentumDiff-Wasm-Path": str(wasm_rel),
        },
        files=[wasm_rel],
        root=tmp_path,
    )
    ep = SimpleNamespace(name="third-party", dist=dist)

    assert _wasm_path_from_ep(ep) == str(wasm_path)


def test_third_party_legacy_pysd_wasm_path_field_is_rejected(tmp_path) -> None:
    legacy_field = "Py" + "sd" + "-Wasm-Path"
    dist = _FakeDist(
        "intentumdiff-third-party",
        metadata={
            "Name": "intentumdiff-third-party",
            "Version": "1.0.0",
            "Author": "Tests",
            legacy_field: "pkg/wasm/my_parser.wasm",
        },
        files=[PurePosixPath("pkg/wasm/my_parser.wasm")],
        root=tmp_path,
    )
    ep = SimpleNamespace(name="third-party", dist=dist)

    with pytest.raises(ValueError, match="IntentumDiff-Wasm-Path"):
        _wasm_path_from_ep(ep)


def test_catalog_discovery_does_not_instantiate_plugins() -> None:
    registry = PluginRegistry(DiffConfig())

    with (
        patch(
            "intentumdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("python"), _entry("sql")],
        ),
        patch("intentumdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch("intentumdiff.plugins.registry.load_plugin") as load_plugin,
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
            "intentumdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("python"), _entry("sql")],
        ),
        patch("intentumdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch("intentumdiff.plugins.registry.load_plugin", side_effect=load) as load_plugin,
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
            "intentumdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("c"), _entry("cpp")],
        ),
        patch("intentumdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch(
            "intentumdiff.plugins.registry.load_plugin",
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
            "intentumdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("freebasic")],
        ),
        patch("intentumdiff.plugins.registry._wasm_path_from_ep") as wasm_path,
    ):
        assert registry._catalog() == []

    wasm_path.assert_not_called()


def test_relevant_parser_load_failure_is_reported() -> None:
    registry = PluginRegistry(DiffConfig())

    with (
        patch(
            "intentumdiff.plugins.registry.importlib.metadata.entry_points",
            return_value=[_entry("python")],
        ),
        patch("intentumdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
        patch("intentumdiff.plugins.registry.load_plugin", side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(PluginNotFoundError):
            registry.detect_parser("example.py", "def f(): pass")

    summary = registry.parser_load_failure_summary()
    assert summary is not None
    assert "boom" in summary


def test_unknown_filename_still_places_generic_last():
    registry = PluginRegistry(DiffConfig())
    with (
        patch("intentumdiff.plugins.registry.importlib.metadata.entry_points", return_value=[_entry("generic"), _entry("python")]),
        patch("intentumdiff.plugins.registry._wasm_path_from_ep", side_effect=_wasm_path),
    ):
        selected = registry._candidate_entries("unknown.extension")
    assert [entry.entry_names for entry in selected] == [["python"], ["generic"]]


def test_shared_routing_corpus():
    import json
    import os
    import subprocess
    from pathlib import Path
    from intentumdiff.rust_core import _required_engine_json
    cases = json.loads((Path(__file__).parents[1] / "fixtures/parser_routing.json").read_text(encoding="utf-8"))
    for case in cases:
        indices = _required_engine_json("parser_candidate_shortlist", json.dumps(case), result_type=list)
        assert [case["entries"][index]["id"] for index in indices] == case["expected"], case["name"]
        if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler":"parser_candidate_shortlist",**case}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            assert native == indices, case["name"]


def test_special_filename_produces_source_judged_diff():
    import json
    import os
    import subprocess
    from pathlib import Path
    from intentumdiff import SemanticDiffer
    root = Path(__file__).parents[2]
    case = json.loads((root / "tests/fixtures/parser_special_filename.json").read_text(encoding="utf-8"))
    actual = SemanticDiffer().diff_strings(case["old"], case["new"], case["filename"]).model_dump(mode="json")
    assert actual["language"] == case["language"]
    assert actual["has_semantic_changes"] is True
    assert actual["is_style_only"] is False
    assert len(actual["changes"]) == 1
    change = actual["changes"][0]
    assert change["change_type"] == "MODIFICATION"
    assert change["old_node"]["label"] == case["old_label"]
    assert change["new_node"]["label"] == case["new_label"]
    if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
        request = {**case, "repo": str(root), "wasm": str(root / "src/intentumdiff/wasm")}
        native = json.loads(subprocess.run([probe], input=json.dumps(request), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
        # Native omits optional null fields; deserialize both into the public DTO.
        from intentumdiff.core.models import SemanticDiff
        native = SemanticDiff.model_validate(native).model_dump(mode="json")
        for field in ("language", "changes", "change_groups", "has_semantic_changes", "is_style_only"):
            assert native[field] == actual[field], field


def test_declining_filename_candidate_reaches_generic():
    registry = PluginRegistry(DiffConfig())
    def load(path, *_args, **_kwargs):
        return _plugin('generic', ['generic'], 'generic') if 'generic' in path else _plugin('python', ['python'], '')
    with patch('intentumdiff.plugins.registry.importlib.metadata.entry_points', return_value=[_entry('python'), _entry('generic')]), patch('intentumdiff.plugins.registry._wasm_path_from_ep', side_effect=lambda ep: f'C:/tmp/{ep.name}_parser.wasm'), patch('intentumdiff.plugins.registry.load_plugin', side_effect=load):
        parser, language = registry.detect_parser('example.py', 'unrecognized syntax')
    assert language == 'generic'
    assert parser.grammar_id == 'generic'


def test_filename_probe_rejects_undeclared_claim():
    registry = PluginRegistry(DiffConfig())
    with patch('intentumdiff.plugins.registry.importlib.metadata.entry_points', return_value=[_entry('python')]), patch('intentumdiff.plugins.registry._wasm_path_from_ep', side_effect=_wasm_path), patch('intentumdiff.plugins.registry.load_plugin', return_value=_plugin('python', ['python'], 'ruby')):
        with pytest.raises(RuntimeError, match='undeclared language'):
            registry.detect_parser('example.py', 'def f(): pass')


@pytest.mark.parametrize('stage', ['load', 'probe'])
@pytest.mark.parametrize('explicit', [False, True])
def test_filename_fuel_error_is_terminal(stage, explicit):
    from intentumdiff.plugins.exceptions import PluginFuelExhausted
    registry = PluginRegistry(DiffConfig())
    failure = PluginFuelExhausted('python', 1)
    component = _plugin('python', ['python'], 'python')
    if stage == 'probe':
        component.call_detect_language.side_effect = failure
    with patch('intentumdiff.plugins.registry.importlib.metadata.entry_points', return_value=[_entry('python'), _entry('sql')]), patch('intentumdiff.plugins.registry._wasm_path_from_ep', side_effect=_wasm_path), patch('intentumdiff.plugins.registry.load_plugin', side_effect=failure if stage == 'load' else None, return_value=component) as loader:
        with pytest.raises(PluginFuelExhausted) as caught:
            registry.detect_parser('example.py', 'def f(): pass', plugin_id='python' if explicit else None)
    assert caught.value is failure
    assert loader.call_count == 1


def test_explicit_filename_probe_error_is_not_no_match():
    registry = PluginRegistry(DiffConfig())
    failure = RuntimeError('broken probe')
    component = _plugin('python', ['python'], 'python')
    component.call_detect_language.side_effect = failure
    with patch('intentumdiff.plugins.registry.importlib.metadata.entry_points', return_value=[_entry('python')]), patch('intentumdiff.plugins.registry._wasm_path_from_ep', side_effect=_wasm_path), patch('intentumdiff.plugins.registry.load_plugin', return_value=component):
        with pytest.raises(RuntimeError, match='broken probe') as caught:
            registry.detect_parser('example.py', 'def f(): pass', plugin_id='python')
    assert caught.value is failure


def test_filename_probe_receives_utf8_bounded_sample():
    registry = PluginRegistry(DiffConfig())
    component = _plugin('python', ['python'], 'python')
    with patch('intentumdiff.plugins.registry.importlib.metadata.entry_points', return_value=[_entry('python')]), patch('intentumdiff.plugins.registry._wasm_path_from_ep', side_effect=_wasm_path), patch('intentumdiff.plugins.registry.load_plugin', return_value=component):
        registry.detect_parser('example.py', 'a' * 2047 + '\U0001f600')
    component.call_detect_language.assert_called_once_with('example.py', 'a' * 2047)


@pytest.mark.parametrize('gate', ['provenance', 'osv'])
def test_filename_security_denial_is_terminal(gate, tmp_path, monkeypatch):
    import json
    import time
    from intentumdiff.plugins import loader as module
    from intentumdiff.plugins.exceptions import PluginLoadError
    registry = PluginRegistry(DiffConfig())
    monkeypatch.delenv('INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME', raising=False)
    if gate == 'provenance':
        wasm = tmp_path / 'python.wasm'
        wasm.write_bytes(b'not-the-pinned-artifact')
        manifest = tmp_path / 'provenance.json'
        manifest.write_text(json.dumps({'artifacts':{'python.wasm':{'sha256':'0'*64}}}), encoding='utf-8')
        monkeypatch.setattr(module, '_PROVENANCE_MANIFEST', manifest)
        monkeypatch.setattr(module, '_is_trusted_wasm_path', lambda path: True)
        monkeypatch.setenv('INTENTUMDIFF_ENFORCE_WASM_PROVENANCE', '1')
        check = lambda: module._verify_builtin_provenance(wasm)
    else:
        monkeypatch.setattr(module, '_read_stamp', lambda: time.time())
        monkeypatch.setattr(module, '_load_osv_cache', lambda: None)
        monkeypatch.setattr(module, '_is_trusted_wasm_path', lambda path: False)
        check = lambda: module._check_osv_cache_or_block('third-party.wasm')
    with pytest.raises(PluginLoadError) as denied:
        check()
    with patch('intentumdiff.plugins.registry.importlib.metadata.entry_points', return_value=[_entry('python'), _entry('generic')]), patch('intentumdiff.plugins.registry._wasm_path_from_ep', side_effect=lambda ep: f'C:/tmp/{ep.name}_parser.wasm'), patch('intentumdiff.plugins.registry.load_plugin', side_effect=denied.value) as load:
        with pytest.raises(PluginLoadError) as propagated:
            registry.detect_parser('example.py', 'def f(): pass')
    assert propagated.value is denied.value
    assert load.call_count == 1
