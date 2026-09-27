"""Filesystem adapter for the shared Rust compile-context API."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def compile_commands_metadata(*, filename: str, language: str, cwd: Path | None = None) -> dict[str, Any] | None:
    from intentumdiff.rust_core import _c_abi_call
    if language.lower() not in {"c", "cpp", "c++", "cxx"} or filename.startswith("<"):
        return None
    base_dir = (cwd or Path.cwd()).resolve()
    file_path = Path(filename)
    if not file_path.is_absolute():
        file_path = base_dir / file_path
    for directory in (file_path.parent, *file_path.parents):
        database = directory / "compile_commands.json"
        if database.is_file():
            try:
                contents = json.loads(database.read_text(encoding="utf8"))
            except (OSError, json.JSONDecodeError):
                return None
            return _c_abi_call("compile_context", json.dumps({
                "database": contents, "database_path": str(database), "filename": filename,
                "language": language, "cwd": str(base_dir),
            }))
    return None
