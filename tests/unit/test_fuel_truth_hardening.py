"""Release-blocking fuel truth checks for small, repo-shaped diffs."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pytest

from intentdiff import SemanticDiffer
from intentdiff.core.models import ChangeType, DiffConfig
# The absolute cap tracks the engine's hotspot policy: the floor moved with
# literal-container capture (#46) and swings ±10-15% with whole-binary LTO codegen
# jitter across rebuilds (typescript tiny-file measured 20.45M, 2026-07).
from intentdiff.differ import _FUEL_HOTSPOT_ABSOLUTE
from intentdiff.plugins.exceptions import PluginFuelExhausted

pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "crates" / "parsers").exists(),
    reason="monorepo crates tree not present (#82 split python repo)",
)


_MAIN_TS_BEFORE = """import { app, BrowserWindow } from "electron";
import { readFileSync } from "fs";
import { modelFromArtifact, renderReviewShell } from "./reviewArtifact";
import { loadReviewArtifactFromArgs } from "./mainModel";

async function createWindow(): Promise<void> {
  const artifact = loadReviewArtifactFromArgs(process.argv, (path) => readFileSync(path, "utf8"));
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(renderReviewShell(modelFromArtifact(artifact)))}`);
}

void app.whenReady().then(createWindow);
"""

_MAIN_TS_AFTER = _MAIN_TS_BEFORE + "\n\nvoid app.whenReady().then(createWindow);\n"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DUPLICATE_CHILD_TRAVERSAL = re.compile(
    r"if !is_semantic\(&node\.node_type\) \{\s+"
    r"let children: Vec<SemanticNode> = node.*?"
    r"if children\.is_empty\(\) \{\s+return None;\s+\}\s+"
    r"\}\s+"
    r"let children: Vec<SemanticNode> = node",
    re.S,
)


@pytest.fixture(scope="module")
def differ() -> SemanticDiffer:
    return SemanticDiffer(DiffConfig(diagnostics=True))


def test_typescript_duplicate_main_ready_line_is_small_addition_not_fuel_hotspot(
    differ: SemanticDiffer,
) -> None:
    diff = differ.diff_strings(
        _MAIN_TS_BEFORE,
        _MAIN_TS_AFTER,
        filename="apps/review-shell/src/main.ts",
        language_hint="typescript",
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert _change_types(diff) == {ChangeType.ADDITION.value}
    assert any("app.whenReady" in (change.new_node.label if change.new_node else "") or "expression_statement" in change.description for change in diff.changes)
    assert _fuel_hotspots(diff) == []
    assert _max_process_fuel(diff) < _FUEL_HOTSPOT_ABSOLUTE


def test_typescript_repeated_tiny_functions_stay_under_fuel_policy(
    differ: SemanticDiffer,
) -> None:
    old = _typescript_repeated_functions(20)
    new = old + "\nexport function inserted(value: number): number { return value + 1; }\n"

    diff = differ.diff_strings(
        old,
        new,
        filename="generated/tiny-functions.ts",
        language_hint="typescript",
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert _change_types(diff) == {ChangeType.ADDITION.value}
    assert any(
        change.new_node and change.new_node.label == "inserted"
        for change in diff.changes
    )
    assert _fuel_hotspots(diff) == []
    assert _max_process_fuel(diff) < _FUEL_HOTSPOT_ABSOLUTE


@pytest.mark.parametrize(
    ("language", "filename", "old", "new"),
    [
        ("typescript", "tiny.ts", "export const answer = 41;\n", "export const answer = 42;\n"),
        ("rust", "tiny.rs", "fn answer() -> i32 { 41 }\n", "fn answer() -> i32 { 42 }\n"),
        (
            "powershell",
            "tiny.ps1",
            'function Get-Answer { return "old" }\n',
            'function Get-Answer { return "new" }\n',
        ),
        ("markdown", "tiny.md", "# Title\n\nOld body.\n", "# Title\n\nNew body.\n"),
        ("mdx", "tiny.mdx", "# Title\n\n<Answer value=\"old\" />\n", "# Title\n\n<Answer value=\"new\" />\n"),
    ],
)
def test_priority_languages_have_tiny_diff_fuel_headroom(
    differ: SemanticDiffer,
    language: str,
    filename: str,
    old: str,
    new: str,
) -> None:
    diff = differ.diff_strings(old, new, filename=filename, language_hint=language)

    assert not any("FUEL_EXCEEDED" in error for error in diff.parse_errors)
    assert not diff.is_fallback
    assert diff.changes or diff.change_groups or diff.is_style_only
    assert _fuel_hotspots(diff) == []


def test_every_supported_language_example_has_no_excessive_fuel_hotspot(
    differ: SemanticDiffer,
) -> None:
    failures: list[tuple[str, list[dict[str, Any]]]] = []
    for language in differ.supported_languages():
        example = differ.playground_example(language)
        if example is None:
            continue
        old = example.get("old", "")
        new = example.get("new", "")
        if not old or not new:
            continue
        diff = differ.diff_strings(
            old,
            new,
            filename=_default_filename(differ, language),
            language_hint=language,
        )
        hotspots = _fuel_hotspots(diff)
        if hotspots:
            failures.append((language, hotspots))

    assert failures == []


def test_tiny_wasm_fuel_fails_explicitly_without_fake_semantics() -> None:
    differ = SemanticDiffer(DiffConfig(plugin_fuel=1_000, diagnostics=True))

    with pytest.raises(PluginFuelExhausted, match="FUEL_EXCEEDED"):
        differ.diff_strings(
            "export const answer = 41;\n",
            "export const answer = 42;\n",
            filename="fuel.ts",
            language_hint="typescript",
        )


def test_realistic_low_fuel_setting_adapts_for_rust_parser_sized_file() -> None:
    old = _rust_repeated_functions(120)
    new = old + "\npub fn inserted(value: i32) -> i32 { value + 1 }\n"
    differ = SemanticDiffer(DiffConfig(plugin_fuel=10_000_000, diagnostics=True))

    diff = differ.diff_strings(
        old,
        new,
        filename="crates/parsers/example-parser/src/lib.rs",
        language_hint="rust",
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert _change_types(diff) == {ChangeType.ADDITION.value}
    assert any(
        change.new_node and change.new_node.label == "inserted"
        for change in diff.changes
    )


def test_real_js_ts_parser_source_does_not_exhaust_configured_10m_fuel_floor() -> None:
    parser_source_path = _REPO_ROOT / "crates" / "parsers" / "js-ts-parser" / "src" / "lib.rs"
    old = parser_source_path.read_text(encoding="utf-8")
    new = old + "\nfn intentdiff_fuel_probe() -> i32 { 1 }\n"
    differ = SemanticDiffer(DiffConfig(plugin_fuel=10_000_000, diagnostics=True))

    diff = differ.diff_strings(
        old,
        new,
        filename="crates/parsers/js-ts-parser/src/lib.rs",
        language_hint="rust",
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert _change_types(diff) == {ChangeType.ADDITION.value}
    assert _fuel_hotspots(diff) == []


def test_repeated_powershell_functions_do_not_emit_fuel_hotspots(
    differ: SemanticDiffer,
) -> None:
    old = _powershell_repeated_functions(80)
    new = old + (
        "function Get-Inserted {\n"
        "  param([int]$Value)\n"
        "  return $Value + 1\n"
        "}\n"
    )

    diff = differ.diff_strings(
        old,
        new,
        filename="generated/helpers.ps1",
        language_hint="powershell",
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert _change_types(diff) == {ChangeType.ADDITION.value}
    assert _fuel_hotspots(diff) == []


@pytest.mark.parametrize(
    ("language", "filename", "make_old", "mutate"),
    [
        (
            "typescript",
            "generated.ts",
            lambda: _typescript_repeated_functions(30),
            lambda old: old
            + "\nexport function inserted(value: number): number { return value + 1; }\n",
        ),
        (
            "rust",
            "generated.rs",
            lambda: _rust_repeated_functions(30),
            lambda old: old + "\npub fn inserted(value: i32) -> i32 { value + 1 }\n",
        ),
        (
            "powershell",
            "generated.ps1",
            lambda: _powershell_repeated_functions(30),
            lambda old: old
            + "function Get-Inserted {\n"
            + "  param([int]$Value)\n"
            + "  return $Value + 1\n"
            + "}\n",
        ),
        (
            "python",
            "generated.py",
            lambda: _python_repeated_functions(30),
            lambda old: old + "def inserted(value):\n    return value + 1\n",
        ),
        (
            "go",
            "generated.go",
            lambda: _go_repeated_functions(30),
            lambda old: old + "\nfunc Inserted(value int) int { return value + 1 }\n",
        ),
        (
            "java",
            "Generated.java",
            lambda: _java_repeated_methods(30),
            lambda old: _insert_java_method(old),
        ),
        (
            "cpp",
            "generated.cpp",
            lambda: _cpp_repeated_functions(30),
            lambda old: old + "\nint inserted(int value) { return value + 1; }\n",
        ),
        (
            "csharp",
            "Generated.cs",
            lambda: _csharp_repeated_methods(30),
            lambda old: _insert_csharp_method(old),
        ),
        (
            "markdown",
            "generated.md",
            lambda: _markdown_repeated_sections(30),
            lambda old: old + "## Inserted\n\nNew body.\n",
        ),
        (
            "mdx",
            "generated.mdx",
            lambda: _mdx_repeated_sections(30),
            lambda old: old + "## Inserted\n\n<Component value=\"new\" />\n",
        ),
    ],
)
def test_generated_repeated_constructs_do_not_emit_fuel_hotspots(
    differ: SemanticDiffer,
    language: str,
    filename: str,
    make_old: Any,
    mutate: Any,
) -> None:
    old = make_old()
    new = mutate(old)
    diff = differ.diff_strings(old, new, filename=filename, language_hint=language)

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    assert _fuel_hotspots(diff) == []


def test_parser_sources_do_not_duplicate_nonsemantic_child_traversal() -> None:
    offenders = []
    for path in (_REPO_ROOT / "crates" / "parsers").glob("*/src/lib.rs"):
        text = path.read_text(encoding="utf-8")
        if _DUPLICATE_CHILD_TRAVERSAL.search(text):
            offenders.append(str(path.relative_to(_REPO_ROOT)))

    assert offenders == []


def _default_filename(differ: SemanticDiffer, language: str) -> str:
    for group in differ.language_info():
        if group.language != language or not group.plugins:
            continue
        selected = next(
            (plugin for plugin in group.plugins if plugin.plugin_id == group.selected_plugin_id),
            group.plugins[0],
        )
        return selected.default_filename
    return f"example.{language}"


def _fuel_hotspots(diff: Any) -> list[dict[str, Any]]:
    telemetry = diff.metadata.get("engine_telemetry", {})
    hotspots = telemetry.get("fuel_hotspots", [])
    return hotspots if isinstance(hotspots, list) else []


def _max_process_fuel(diff: Any) -> int:
    telemetry = diff.metadata.get("engine_telemetry", {})
    calls = telemetry.get("calls", [])
    return max(
        (
            call.get("fuel_consumed") or 0
            for call in calls
            if call.get("function") == "process"
        ),
        default=0,
    )


def _change_types(diff: Any) -> set[str]:
    return {getattr(change.change_type, "value", change.change_type) for change in diff.changes}


def _typescript_repeated_functions(count: int) -> str:
    return "\n".join(
        f"export function helper{i}(value: number): number {{ return value + {i}; }}"
        for i in range(count)
    ) + "\n"


def _rust_repeated_functions(count: int) -> str:
    return "\n".join(
        f"pub fn helper{i}(value: i32) -> i32 {{ value + {i} }}"
        for i in range(count)
    ) + "\n"


def _powershell_repeated_functions(count: int) -> str:
    return "\n".join(
        (
            f"function Get-Helper{i} {{\n"
            "  param([int]$Value)\n"
            f"  return $Value + {i}\n"
            "}\n"
        )
        for i in range(count)
    ) + "\n"


def _python_repeated_functions(count: int) -> str:
    return "\n".join(
        f"def helper_{i}(value):\n    return value + {i}\n"
        for i in range(count)
    ) + "\n"


def _go_repeated_functions(count: int) -> str:
    return "package demo\n\n" + "\n".join(
        f"func Helper{i}(value int) int {{ return value + {i} }}"
        for i in range(count)
    ) + "\n"


def _java_repeated_methods(count: int) -> str:
    methods = "\n".join(
        f"  public int helper{i}(int value) {{ return value + {i}; }}"
        for i in range(count)
    )
    return f"public class Generated {{\n{methods}\n}}\n"


def _insert_java_method(source: str) -> str:
    idx = source.rfind("}\n")
    return (
        source[:idx]
        + "  public int inserted(int value) { return value + 1; }\n"
        + source[idx:]
    )


def _cpp_repeated_functions(count: int) -> str:
    return "\n".join(
        f"int helper{i}(int value) {{ return value + {i}; }}"
        for i in range(count)
    ) + "\n"


def _csharp_repeated_methods(count: int) -> str:
    methods = "\n".join(
        f"  int Helper{i}(int value) {{ return value + {i}; }}"
        for i in range(count)
    )
    return f"class Generated {{\n{methods}\n}}\n"


def _insert_csharp_method(source: str) -> str:
    idx = source.rfind("}\n")
    return source[:idx] + "  int Inserted(int value) { return value + 1; }\n" + source[idx:]


def _markdown_repeated_sections(count: int) -> str:
    return "\n".join(f"## Section {i}\n\nBody {i}.\n" for i in range(count)) + "\n"


def _mdx_repeated_sections(count: int) -> str:
    return "\n".join(
        f"## Section {i}\n\n<Component value=\"{i}\" />\n"
        for i in range(count)
    ) + "\n"
