"""Correctness tests for defects found by the 0.0.2 independent release review.

Every assertion here is against the EXPECTED result (the diff-expectations oracle), never
against agreement between two engines: both runtimes share one Core, so parity tests stayed
green while both were wrong.

- buchochelliq-labs/intentumdiff-core#229 / #282: operator changes reported as style-only.
- #280: ``git --format json`` with no changes prints text and writes no report.
- #281: plugin host runs a plugin with no CPU cap when ``set_fuel`` fails.
- #284: SARIF regions clamp 0-based positions instead of converting them to 1-based.

The module locates components through the imported package (not the repo path), so it
runs unchanged against a source checkout with staged components or an installed wheel.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import intentumdiff

_WASM_DIR = Path(intentumdiff.__file__).resolve().parent / "wasm"
_HAS_COMPONENTS = (_WASM_DIR / "python_parser.wasm").exists()
needs_components = pytest.mark.skipif(
    not _HAS_COMPONENTS, reason="first-party parser components are not staged"
)


# ---------------------------------------------------------------------------
# Core #229 — an operator change is behaviour, never style
# ---------------------------------------------------------------------------

# (language id, extension, old, new). Each pair differs ONLY in an operator token.
_OPERATOR_CASES = [
    ("python-sub", "py", "def f(a, b):\n    return a + b\n", "def f(a, b):\n    return a - b\n"),
    ("python-mul", "py", "def f(a, b):\n    return a + b\n", "def f(a, b):\n    return a * b\n"),
    ("python-cmp", "py", "def f(a, b):\n    return a < b\n", "def f(a, b):\n    return a > b\n"),
    ("python-bool", "py", "def f(a, b):\n    return a and b\n", "def f(a, b):\n    return a or b\n"),
    ("python-augassign", "py", "x = 1\nx += 2\n", "x = 1\nx -= 2\n"),
    ("javascript", "js", "function f(a, b) {\n  return a + b;\n}\n", "function f(a, b) {\n  return a - b;\n}\n"),
    ("typescript", "ts", "function f(a: number, b: number) {\n  return a + b;\n}\n",
     "function f(a: number, b: number) {\n  return a - b;\n}\n"),
    ("go", "go", "package m\n\nfunc F(a, b int) int {\n\treturn a + b\n}\n",
     "package m\n\nfunc F(a, b int) int {\n\treturn a - b\n}\n"),
    ("rust", "rs", "fn f(a: i32, b: i32) -> i32 {\n    a + b\n}\n", "fn f(a: i32, b: i32) -> i32 {\n    a - b\n}\n"),
    ("java", "java", "class A {\n  int f(int a, int b) {\n    return a + b;\n  }\n}\n",
     "class A {\n  int f(int a, int b) {\n    return a - b;\n  }\n}\n"),
    ("c", "c", "int f(int a, int b) {\n  return a + b;\n}\n", "int f(int a, int b) {\n  return a - b;\n}\n"),
    ("cpp", "cpp", "int f(int a, int b) {\n  return a + b;\n}\n", "int f(int a, int b) {\n  return a - b;\n}\n"),
    ("csharp", "cs", "class A {\n  int F(int a, int b) { return a + b; }\n}\n",
     "class A {\n  int F(int a, int b) { return a - b; }\n}\n"),
    ("ruby", "rb", "def f(a, b)\n  a + b\nend\n", "def f(a, b)\n  a - b\nend\n"),
    ("php", "php", "<?php\nfunction f($a, $b) {\n  return $a + $b;\n}\n",
     "<?php\nfunction f($a, $b) {\n  return $a - $b;\n}\n"),
    ("scala", "scala", "object A {\n  def f(a: Int, b: Int): Int = a + b\n}\n",
     "object A {\n  def f(a: Int, b: Int): Int = a - b\n}\n"),
    ("elixir", "ex", "defmodule A do\n  def f(a, b), do: a + b\nend\n", "defmodule A do\n  def f(a, b), do: a - b\nend\n"),
    ("dart", "dart", "int f(int a, int b) {\n  return a + b;\n}\n", "int f(int a, int b) {\n  return a - b;\n}\n"),
    ("zig", "zig", "fn f(a: i32, b: i32) i32 {\n    return a + b;\n}\n", "fn f(a: i32, b: i32) i32 {\n    return a - b;\n}\n"),
    ("r", "r", "f <- function(a, b) {\n  a + b\n}\n", "f <- function(a, b) {\n  a - b\n}\n"),
    # Already correct on the 0.0.2 candidate; kept so a fix cannot regress them.
    ("sql", "sql", "SELECT a FROM t WHERE x > 1;\n", "SELECT a FROM t WHERE x < 1;\n"),
    ("kotlin", "kt", "fun f(a: Int, b: Int): Int {\n    return a + b\n}\n", "fun f(a: Int, b: Int): Int {\n    return a - b\n}\n"),
    ("lua", "lua", "function f(a, b)\n  return a + b\nend\n", "function f(a, b)\n  return a - b\nend\n"),
    ("haskell", "hs", "f :: Int -> Int -> Int\nf a b = a + b\n", "f :: Int -> Int -> Int\nf a b = a - b\n"),
]


@needs_components
@pytest.mark.parametrize(("case", "ext", "old", "new"), _OPERATOR_CASES, ids=[c[0] for c in _OPERATOR_CASES])
def test_operator_change_is_a_semantic_change(case: str, ext: str, old: str, new: str) -> None:
    from intentumdiff.differ import SemanticDiffer

    diff = SemanticDiffer().diff_strings(old, new, f"example.{ext}")
    assert diff.is_style_only is False, f"{case}: operator change reported as style-only"
    assert diff.has_semantic_changes is True, f"{case}: operator change has no semantic changes"
    assert diff.changes, f"{case}: no change reported for an operator edit"
    assert not any(g.kind == "IGNORED_STYLE" for g in diff.change_groups), (
        f"{case}: operator change filed as IGNORED_STYLE"
    )


# Controls: the fix must keep genuine layout/comment edits style-only.
_STYLE_CONTROLS = [
    ("python-whitespace", "py", "def f(a, b):\n    return a + b\n", "def f(a, b):\n    return a  +  b\n"),
    ("python-comment", "py", "def f(a, b):\n    return a + b\n", "def f(a, b):\n    # sum\n    return a + b\n"),
    ("javascript-whitespace", "js", "function f(a, b) {\n  return a + b;\n}\n",
     "function f(a, b) {\n  return a  +  b;\n}\n"),
    ("go-comment", "go", "package m\n\nfunc F(a, b int) int {\n\treturn a + b\n}\n",
     "package m\n\nfunc F(a, b int) int {\n\t// sum\n\treturn a + b\n}\n"),
]


@needs_components
@pytest.mark.parametrize(("case", "ext", "old", "new"), _STYLE_CONTROLS, ids=[c[0] for c in _STYLE_CONTROLS])
def test_layout_and_comment_edits_stay_style_only(case: str, ext: str, old: str, new: str) -> None:
    from intentumdiff.differ import SemanticDiffer

    diff = SemanticDiffer().diff_strings(old, new, f"example.{ext}")
    assert diff.has_semantic_changes is False, f"{case}: layout edit reported as semantic"
    assert diff.is_style_only is True, f"{case}"


# ---------------------------------------------------------------------------
# #280 — `git --format json` with nothing changed must still emit JSON / the report file
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "v1")
    return repo


def _run_cli(*argv: str) -> int:
    from intentumdiff.cli import main

    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    return int(exc.value.code or 0)


def test_git_json_with_no_changes_prints_json(clean_repo: Path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(clean_repo)
    code = _run_cli("git", "--format", "json")
    out = capsys.readouterr().out
    assert code == 0
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        pytest.fail(f"--format json printed non-JSON for an unchanged repo: {out!r}")
    assert payload == [] or payload.get("diffs", None) == [], payload


def test_git_json_with_no_changes_writes_output_file(clean_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(clean_repo)
    report = tmp_path / "report.json"
    assert _run_cli("git", "--format", "json", "--output", str(report)) == 0
    assert report.exists(), "--output file was not written for an unchanged repo"
    json.loads(report.read_text(encoding="utf-8"))


def test_git_with_no_changes_still_writes_guardrail_reports(clean_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(clean_repo)
    sarif = tmp_path / "guardrails.sarif"
    gjson = tmp_path / "guardrails.json"
    assert _run_cli("git", "--format", "json", "--guardrails-sarif", str(sarif),
                    "--guardrails-json", str(gjson)) == 0
    assert sarif.exists(), "SARIF report missing when nothing changed (CI upload step would fail)"
    assert gjson.exists(), "guardrail JSON report missing when nothing changed"


# ---------------------------------------------------------------------------
# #281 — a plugin must never run without its fuel cap
# ---------------------------------------------------------------------------


@needs_components
def test_plugin_call_fails_closed_when_fuel_cannot_be_set(monkeypatch) -> None:
    import wasmtime

    from intentumdiff.plugins.exceptions import PluginError
    from intentumdiff.plugins.loader import load_plugin

    plugin = load_plugin(str(_WASM_DIR / "python_parser.wasm"), trusted=True)
    plugin.call_parser_mode()  # sanity: the component works with a fuel cap

    invoked: list[str] = []
    real_get_func = plugin._instance.get_func

    def recording_get_func(store, idx):
        invoked.append("guest export resolved")
        return real_get_func(store, idx)

    def broken_set_fuel(self, fuel):
        raise wasmtime.WasmtimeError("simulated: fuel consumption not enabled")

    monkeypatch.setattr(plugin._instance, "get_func", recording_get_func)
    monkeypatch.setattr(wasmtime.Store, "set_fuel", broken_set_fuel)

    with pytest.raises(PluginError):
        plugin.call_parser_mode()
    assert not invoked, "guest code was invoked although the fuel cap could not be applied"


# ---------------------------------------------------------------------------
# #284 — SARIF regions are 1-based; engine positions are 0-based
# ---------------------------------------------------------------------------


def _violation_at(line0: int, col0: int):
    from intentumdiff.core.models import (
        GuardrailCheckResult,
        GuardrailSeverity,
        GuardrailViolation,
        NodePosition,
    )

    violation = GuardrailViolation(
        rule_id="protected.key",
        severity=next(iter(GuardrailSeverity)),
        file="config.yaml",
        semantic_path="spec.key",
        position=NodePosition(start_line=line0, start_col=col0, end_line=line0, end_col=col0 + 5),
    )
    return GuardrailCheckResult(violations=[violation])


def test_sarif_region_converts_zero_based_positions() -> None:
    from intentumdiff.analysis.guardrail_reports import sarif_from_guardrail_result

    # 0-based (line 2, col 4) is the 3rd line, 5th column in SARIF terms.
    sarif = sarif_from_guardrail_result(_violation_at(2, 4))
    region = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
    assert (region["startLine"], region["startColumn"]) == (3, 5), region
    assert (region["endLine"], region["endColumn"]) == (3, 10), region


def test_github_annotation_converts_zero_based_positions() -> None:
    from intentumdiff.analysis.guardrail_reports import render_guardrail_annotations

    text = render_guardrail_annotations(_violation_at(2, 4))
    assert "line=3" in text and "col=5" in text, text
