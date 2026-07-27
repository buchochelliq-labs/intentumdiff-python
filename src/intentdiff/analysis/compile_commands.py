"""Compile database metadata for C/C++ review context."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

_CXX_LANGUAGES = frozenset({"c", "cpp", "c++", "cxx"})
_COMPILE_DATABASE = "compile_commands.json"


def compile_commands_metadata(
    *,
    filename: str,
    language: str,
    cwd: Path | None = None,
) -> dict[str, Any] | None:
    """Return normalized compile-command context for a C/C++ file if available."""

    if language.lower() not in _CXX_LANGUAGES:
        return None
    logical_path = Path(filename)
    if str(logical_path).startswith("<"):
        return None
    base_dir = cwd or Path.cwd()
    file_path = logical_path if logical_path.is_absolute() else base_dir / logical_path
    database = _find_compile_database(file_path)
    if database is None:
        return None
    entry = _matching_entry(database, file_path)
    if entry is None:
        return None
    arguments = _entry_arguments(entry)
    if not arguments:
        return None
    metadata = {
        "database": _repo_relative(database, base_dir),
        "file": _repo_relative(_entry_file_path(entry, database.parent), base_dir),
        "directory": _repo_relative(Path(str(entry.get("directory") or database.parent)), base_dir),
        "arguments": arguments,
        "defines": _flag_values(arguments, "-D"),
        "include_dirs": _include_dirs(arguments),
        "standard": _standard(arguments),
    }
    metadata["fingerprint"] = _fingerprint(metadata)
    return metadata


def _find_compile_database(file_path: Path) -> Path | None:
    for directory in [file_path.parent, *file_path.parents]:
        candidate = directory / _COMPILE_DATABASE
        if candidate.is_file():
            return candidate
    return None


def _matching_entry(database: Path, file_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(database.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, list):
        return None
    target = _normalized_path(file_path)
    basename = file_path.name.lower()
    basename_match: dict[str, Any] | None = None
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry_file = _entry_file_path(item, database.parent)
        if _normalized_path(entry_file) == target:
            return item
        if entry_file.name.lower() == basename and basename_match is None:
            basename_match = item
    return basename_match


def _entry_file_path(entry: dict[str, Any], database_dir: Path) -> Path:
    raw_file = Path(str(entry.get("file") or ""))
    directory = Path(str(entry.get("directory") or database_dir))
    return raw_file if raw_file.is_absolute() else directory / raw_file


def _entry_arguments(entry: dict[str, Any]) -> list[str]:
    arguments = entry.get("arguments")
    if isinstance(arguments, list):
        return [str(arg) for arg in arguments if str(arg)]
    command = entry.get("command")
    if isinstance(command, str) and command.strip():
        try:
            return shlex.split(command, posix=False)
        except ValueError:
            return command.split()
    return []


def _flag_values(arguments: list[str], prefix: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg == prefix and index + 1 < len(arguments):
            values.append(arguments[index + 1])
            index += 2
            continue
        if arg.startswith(prefix) and len(arg) > len(prefix):
            values.append(arg[len(prefix):])
        index += 1
    return values


def _include_dirs(arguments: list[str]) -> list[str]:
    values = _flag_values(arguments, "-I")
    values.extend(_flag_values(arguments, "/I"))
    return values


def _standard(arguments: list[str]) -> str | None:
    for arg in arguments:
        if arg.startswith("-std="):
            return arg.removeprefix("-std=")
        if arg.startswith("/std:"):
            return arg.removeprefix("/std:")
    return None


def _fingerprint(metadata: dict[str, Any]) -> str:
    payload = {
        key: metadata[key]
        for key in ("file", "arguments", "defines", "include_dirs", "standard")
        if metadata.get(key)
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _normalized_path(path: Path) -> str:
    try:
        return str(path.resolve()).replace("\\", "/").lower()
    except OSError:
        return str(path.absolute()).replace("\\", "/").lower()


def _repo_relative(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()
