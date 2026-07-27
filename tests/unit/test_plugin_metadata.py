"""Parser metadata manifest contract tests."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from intentdiff.plugins.adapter import ParserAdapter
from intentdiff.plugins.loader import _language_info_record_to_dict
import pytest

pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "crates" / "parsers").exists(),
    reason="monorepo crates tree not present (#82 split python repo)",
)


ROOT = Path(__file__).resolve().parents[2]
PARSER_LIBS = [
    path
    for base in (ROOT / "crates", ROOT / "plugins" / "intentdiff_dbt" / "crates")
    for path in base.glob("*/src/lib.rs")
    if re.search(
        r"fn\s+language_info\(\)\s*->\s*Vec<LanguageInfoRecord>",
        path.read_text(encoding="utf-8"),
    )
]
RENDERER_LIBS = [
    path
    for path in (ROOT / "crates").glob("*-renderer/src/lib.rs")
    if "world: \"renderer-plugin\"" in path.read_text(encoding="utf-8")
]


def _language_ids(source: str) -> list[str]:
    match = re.search(
        r"fn\s+language_ids\(\)\s*->\s*Vec<String>\s*\{(?P<body>.*?)\n\s*\}",
        source,
        re.S,
    )
    assert match is not None
    return re.findall(r'"([^"]+)"\.to_string\(\)', match.group("body"))


def _sections(metadata: str) -> set[str]:
    return set(re.findall(r"^\[language\.([^\]]+)\]$", metadata, re.M))


class _FakeParserPlugin:
    trusted = True
    wasm_path = "fake.wasm"

    def __init__(
        self,
        *,
        language_ids: list[str],
        language_info: list[dict[str, object]] | Exception,
    ) -> None:
        self._language_ids = language_ids
        self._language_info = language_info

    def call_grammar_id(self) -> str:
        return "python"

    def call_language_ids(self) -> list[str]:
        return self._language_ids

    def call_priority(self, _kind: str = "parser") -> int:
        return 0

    def call_language_info(self) -> list[dict[str, object]]:
        if isinstance(self._language_info, Exception):
            raise self._language_info
        return self._language_info


def _adapter_for(
    language_info: list[dict[str, object]] | Exception,
    *,
    language_ids: list[str] | None = None,
) -> ParserAdapter:
    adapter = ParserAdapter(
        _FakeParserPlugin(
            language_ids=language_ids or ["python"],
            language_info=language_info,
        )
    )
    adapter.plugin_id = "intentdiff:python:python"
    adapter.package_version = "1.2.3"
    adapter.author = "Package Author"
    adapter.provenance = "IntentDiff 1.2.3"
    return adapter


def test_parser_crates_bundle_metadata_for_every_language_id():
    assert PARSER_LIBS
    for lib in PARSER_LIBS:
        source = lib.read_text(encoding="utf-8")
        metadata_path = lib.parent.parent / "plugin_metadata.info"
        assert metadata_path.exists(), f"missing {metadata_path}"
        metadata = metadata_path.read_text(encoding="utf-8")

        assert "include_str!(\"../plugin_metadata.info\")" in source
        assert set(_language_ids(source)) <= _sections(metadata), metadata_path
        assert "[plugin]" in metadata
        assert "last_updated = 2026-05-19" in metadata


def test_cli_entry_points_include_intentdiff_name():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["intentdiff"] == "intentdiff.cli:main"
    assert set(scripts) == {"intentdiff"}


def test_parser_language_info_no_longer_hardcodes_display_metadata():
    forbidden = (
        'author: "IntentDiff"',
        'plugin_version: "0.1.0"',
        'last_updated: "2026-05-19"',
        "fn display_language_name(",
        "fn monaco_language(",
        "fn default_filename(",
        "fn language_file_extensions(",
    )

    for lib in PARSER_LIBS:
        source = lib.read_text(encoding="utf-8")
        for text in forbidden:
            assert text not in source, f"{text!r} still hardcoded in {lib}"


def test_renderer_crates_bundle_metadata_for_future_host_use():
    assert RENDERER_LIBS
    for lib in RENDERER_LIBS:
        source = lib.read_text(encoding="utf-8")
        metadata_path = lib.parent.parent / "plugin_metadata.info"
        assert metadata_path.exists(), f"missing {metadata_path}"
        metadata = metadata_path.read_text(encoding="utf-8")
        assert "include_str!(\"../plugin_metadata.info\")" in source
        assert "[plugin]" in metadata
        assert re.search(r"^\[renderer\.[^\]]+\]$", metadata, re.M), metadata_path


def test_language_info_ignores_unclaimed_language_records():
    adapter = _adapter_for(
        [
            {
                "language_id": "python",
                "language_name": "Python Custom",
                "language_short_name": "Py",
                "monaco_language": "python",
                "default_filename": "custom.py",
                "language_file_extensions": [".py"],
                "author": "Plugin Author",
                "plugin_version": "9.9.9",
                "last_updated": "2026-05-19",
            },
            {
                "language_id": "shell",
                "language_name": "Shell",
                "language_short_name": "Shell",
                "monaco_language": "shell",
                "default_filename": "script.sh",
                "language_file_extensions": [".sh"],
            },
        ]
    )

    infos = adapter.language_info

    assert [info.language_id for info in infos] == ["python"]
    assert infos[0].language_name == "Python Custom"


def test_language_info_replaces_hostile_or_oversized_fields_with_fallbacks():
    adapter = _adapter_for(
        [
            {
                "language_id": "python",
                "language_name": "x" * 129,
                "language_short_name": "bad\x00name",
                "monaco_language": "../python",
                "default_filename": "../secret.py",
                "language_file_extensions": ["../x", ".py", "bad\\path", ".py"],
                "author": "a" * 129,
                "plugin_version": "1.0\x00bad",
                "last_updated": "not-a-date",
            }
        ]
    )

    info = adapter.language_info[0]

    assert info.language_name == "Python"
    assert info.language_short_name == "Python"
    assert info.monaco_language == "python"
    assert info.default_filename == "code.py"
    assert info.language_file_extensions == [".py"]
    assert info.author == "Package Author"
    assert info.plugin_version == "1.2.3"
    assert info.last_updated == "2026-05-19"


def test_language_info_legacy_export_failure_uses_fallback_metadata():
    adapter = _adapter_for(RuntimeError("language-info missing"))

    info = adapter.language_info[0]

    assert info.language_id == "python"
    assert info.language_name == "Python"
    assert info.monaco_language == "python"
    assert info.default_filename == "code.py"
    assert info.plugin_id == "intentdiff:python:python"


def test_language_info_record_conversion_reads_wit_hyphenated_attributes():
    class WasmtimeRecord:
        pass

    record = WasmtimeRecord()
    setattr(record, "language-id", "m")
    setattr(record, "language-name", "Power Query M")
    setattr(record, "language-short-name", "M")
    setattr(record, "monaco-language", "plaintext")
    setattr(record, "default-filename", "query.m")
    setattr(record, "language-file-extensions", [".m"])
    setattr(record, "author", "IntentDiff")
    setattr(record, "plugin-version", "0.1.0")
    setattr(record, "last-updated", "2026-05-19")

    info = _language_info_record_to_dict(record)

    assert info == {
        "language_id": "m",
        "language_name": "Power Query M",
        "language_short_name": "M",
        "monaco_language": "plaintext",
        "default_filename": "query.m",
        "language_file_extensions": [".m"],
        "author": "IntentDiff",
        "plugin_version": "0.1.0",
        "last_updated": "2026-05-19",
    }
