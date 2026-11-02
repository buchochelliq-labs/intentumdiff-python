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
        try:
            raw = database.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            # A broken link represents an explicitly configured unusable DB,
            # not an absent optional compile database.
            if database.is_symlink():
                raise RuntimeError(f"Cannot read configured compile database {database}: {exc}") from exc
            continue
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"Cannot read configured compile database {database}: {exc}") from exc
        try:
            contents = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed compile database {database}: {exc}") from exc
        return _c_abi_call("compile_context", json.dumps({
            "database": contents, "database_path": str(database), "filename": filename,
            "language": language, "cwd": str(base_dir),
        }))
    return None
