from __future__ import annotations

import json

from intentumdiff.analysis.compile_commands import compile_commands_metadata


def test_compile_commands_metadata_matches_relative_file_and_extracts_flags(tmp_path) -> None:
    source = tmp_path / "src" / "math.cpp"
    source.parent.mkdir()
    source.write_text("int value() { return LIMIT; }\n", encoding="utf8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "src/math.cpp",
                    "arguments": [
                        "clang++",
                        "-std=c++20",
                        "-Iinclude",
                        "-DUSE_FAST=1",
                        "-c",
                        "src/math.cpp",
                    ],
                }
            ]
        ),
        encoding="utf8",
    )

    metadata = compile_commands_metadata(
        filename="src/math.cpp",
        language="cpp",
        cwd=tmp_path,
    )

    assert metadata is not None
    assert metadata["database"] == "compile_commands.json"
    assert metadata["file"] == "src/math.cpp"
    assert metadata["standard"] == "c++20"
    assert metadata["defines"] == ["USE_FAST=1"]
    assert metadata["include_dirs"] == ["include"]
    assert metadata["fingerprint"]


def test_compile_commands_metadata_ignores_non_cxx_languages(tmp_path) -> None:
    (tmp_path / "compile_commands.json").write_text("[]", encoding="utf8")

    assert (
        compile_commands_metadata(
            filename="src/app.py",
            language="python",
            cwd=tmp_path,
        )
        is None
    )


def test_compile_commands_fingerprint_changes_with_compile_arguments(tmp_path) -> None:
    source = tmp_path / "src" / "math.cpp"
    source.parent.mkdir()
    source.write_text("int value() { return LIMIT; }\n", encoding="utf8")
    database = tmp_path / "compile_commands.json"

    def write_database(define: str) -> None:
        database.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "file": "src/math.cpp",
                        "arguments": [
                            "clang++",
                            "-std=c++20",
                            f"-D{define}",
                            "-c",
                            "src/math.cpp",
                        ],
                    }
                ]
            ),
            encoding="utf8",
        )

    write_database("LIMIT=4")
    first = compile_commands_metadata(filename="src/math.cpp", language="cpp", cwd=tmp_path)
    write_database("LIMIT=8")
    second = compile_commands_metadata(filename="src/math.cpp", language="cpp", cwd=tmp_path)

    assert first is not None
    assert second is not None
    assert first["fingerprint"] != second["fingerprint"]


def test_compile_commands_metadata_extracts_from_command_string(tmp_path) -> None:
    source = tmp_path / "src" / "engine.cpp"
    source.parent.mkdir()
    source.write_text("int value() { return LIMIT; }\n", encoding="utf8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "src/engine.cpp",
                    "command": 'clang++ -std=c++17 -Iinclude -DDEBUG "src/engine.cpp"',
                }
            ]
        ),
        encoding="utf8",
    )

    metadata = compile_commands_metadata(
        filename="src/engine.cpp",
        language="cpp",
        cwd=tmp_path,
    )

    assert metadata is not None
    assert metadata["defines"] == ["DEBUG"]
    assert metadata["include_dirs"] == ["include"]
    assert metadata["standard"] == "c++17"
    assert metadata["fingerprint"]


def test_compile_context_never_selects_an_unrelated_same_basename(tmp_path):
    (tmp_path / 'c').mkdir()
    (tmp_path / 'compile_commands.json').write_text(json.dumps([
        {'directory': str(tmp_path), 'file': 'a/main.cpp', 'arguments': ['c++', '-DA=1']},
        {'directory': str(tmp_path), 'file': 'b/main.cpp', 'arguments': ['c++', '-DB=1']},
    ]))
    assert compile_commands_metadata(filename='c/main.cpp', language='cpp', cwd=tmp_path) is None


def test_compile_context_preserves_quoted_spaces_and_resolves_relative_directory(tmp_path):
    (tmp_path / 'build').mkdir()
    (tmp_path / 'src').mkdir()
    (tmp_path / 'compile_commands.json').write_text(json.dumps([
        {'directory': 'build', 'file': '../src/main.cpp',
         'command': 'c++ -I"include with spaces" -DNAME="hello world" ../src/main.cpp'},
    ]))
    result = compile_commands_metadata(filename='src/main.cpp', language='cpp', cwd=tmp_path)
    assert result is not None
    assert result['file'] == 'src/main.cpp'
    assert result['directory'] == 'build'
    assert result['include_dirs'] == ['include with spaces']
    assert result['defines'] == ['NAME=hello world']
