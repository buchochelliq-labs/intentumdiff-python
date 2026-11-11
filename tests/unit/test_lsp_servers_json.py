"""``lsp_servers.json`` loading: one deterministic rule for duplicate languages (#312)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentumdiff.lsp.servers import load_lsp_servers_json


def _write(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "lsp_servers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_duplicate_language_entries_are_rejected_naming_both(tmp_path: Path) -> None:
    path = _write(tmp_path, {
        "one": {"language": "python", "command": ["pylsp"]},
        "two": {"language": "python", "command": ["pyright-langserver", "--stdio"]},
    })
    with pytest.raises(ValueError, match=r"'two' and 'one' both serve language 'python'"):
        load_lsp_servers_json(path)


def test_distinct_languages_keep_file_order(tmp_path: Path) -> None:
    path = _write(tmp_path, {
        "ts": {"language": "typescript", "command": ["typescript-language-server", "--stdio"]},
        "py": {"language": "python", "command": ["pylsp"]},
    })
    entries = load_lsp_servers_json(path)
    assert list(entries) == ["typescript", "python"]
    assert tuple(entries["python"].command) == ("pylsp",)


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_lsp_servers_json(tmp_path / "absent.json") == {}
