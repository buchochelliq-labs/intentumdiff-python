"""Exact-output regression tests for 0.0.2 host-I/O fixes (#280 and #284)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# #285 — Rich markup must not swallow staging status or style filenames
# ---------------------------------------------------------------------------


def test_git_rule_title_preserves_literal_status_and_filename() -> None:
    from types import SimpleNamespace

    from rich.console import Console
    from rich.text import Text
    from io import StringIO
    from intentumdiff.cli._commands import _git_file_rule_title

    diff = SimpleNamespace(
        new_filename="[red]x.py", old_filename=None, staging_status="staged"
    )
    title = _git_file_rule_title(diff)
    assert isinstance(title, Text)
    assert title.plain == "[red]x.py [staged]"
    stream = StringIO()
    Console(file=stream, width=65, force_terminal=False, color_system=None).rule(title)
    assert "[red]x.py [staged]" in stream.getvalue()
