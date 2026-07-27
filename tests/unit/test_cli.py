"""
Unit tests for the intentdiff CLI layer.

Tests cover argument parsing and command-function behaviour.  All external
I/O (git, Wasm, SQLite on-disk) is either mocked or redirected to a
temporary directory so the suite runs without any installed plugins or a
real git repository.
"""

from __future__ import annotations

import argparse
import io
import json
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from intentdiff.cli import (
    _build_parser,
    _cmd_assets_diff,
    _cmd_assets_git,
    _cmd_gist_diff,
    _cmd_github_pr,
    _cmd_shell,
    _cmd_cache_clear,
    _cmd_cache_stats,
    _cmd_diagnostics_hotspots,
    _cmd_diagnostics_query,
    _cmd_diagnostics_summary,
    _cmd_guardrails_check,
    _cmd_patch,
    _cmd_index,
    _differ,
    _emit_asset_payload,
    _emit_phase_profiles,
    _render,
    _render_cli_banner,
    _normalize_argv,
    _run_shell_line,
    _should_show_cli_banner,
)
from intentdiff.core.models import (
    GuardrailSeverity,
    GuardrailViolation,
    SemanticDiff,
)
from intentdiff.core.config import load_project_diff_config
from intentdiff.core.indexer import IndexResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(*args: str) -> argparse.Namespace:
    """Parse CLI args via the real parser and return the namespace."""
    return _build_parser().parse_args(list(args))


def _run(*args: str) -> int:
    """Call main() and return the captured exit code."""
    from intentdiff.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(list(args))
    return exc_info.value.code


def _make_result(**kwargs) -> IndexResult:
    """Build a minimal IndexResult for use in tests."""
    from intentdiff.core.index import SemanticIndex

    sem = SemanticIndex()
    sem.build()
    defaults = dict(
        files_indexed=5,
        files_skipped=2,
        errors=[],
        semantic_index=sem,
        from_cache=False,
    )
    defaults.update(kwargs)
    return IndexResult(**defaults)


def _make_mock_differ():
    """Return a mock SemanticDiffer whose _cache attribute is None."""
    mock = MagicMock()
    mock._cache = None
    return mock


def _guardrail_diff() -> SemanticDiff:
    violation = GuardrailViolation(
        rule_id="prod-host",
        severity=GuardrailSeverity.IMMUTABLE,
        file="config.yaml",
        language="yaml",
        semantic_path="server.host",
        old_value="localhost",
        new_value="prod.example.com",
        message="Production host changed",
    )
    return SemanticDiff(
        old_filename="config.yaml",
        new_filename="config.yaml",
        language="yaml",
        guardrail_violations=[violation],
    )


def test_intentdiff_import_alias_exports_public_api() -> None:
    from intentdiff import DiffConfig, SemanticDiffer
    from intentdiff import DiffConfig as LegacyDiffConfig
    from intentdiff import SemanticDiffer as LegacySemanticDiffer

    assert DiffConfig is LegacyDiffConfig
    assert SemanticDiffer is LegacySemanticDiffer


def test_cli_primary_branding_is_intentdiff(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_parser()

    assert parser.prog == "intentdiff"

    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])

    assert "IntentDiff 0.0.1b1" in capsys.readouterr().out


def test_click_main_version_uses_primary_branding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run("--version") == 0

    assert "IntentDiff 0.0.1b1" in capsys.readouterr().out


def test_click_main_delegates_command_help_to_compatible_parser(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run("git", "--help") == 0

    captured = capsys.readouterr().out
    assert "usage: intentdiff git" in captured
    assert "--staged" in captured


def test_shell_command_and_no_banner_flag_parse() -> None:
    assert _parse("shell").func is _cmd_shell
    assert _parse("--no-banner", "git").no_banner is True
    assert _parse("--no-banner", "shell").no_banner is True
    assert _parse("shell", "--no-banner").no_banner is True


def test_assets_commands_parse_and_json_is_machine_output() -> None:
    from intentdiff.cli import _is_machine_output

    diff_args = _parse(
        "assets",
        "diff",
        "--before",
        "old.png",
        "--after",
        "new.png",
        "--out",
        "out",
        "--json",
    )
    assert diff_args.func is _cmd_assets_diff
    assert diff_args.before == "old.png"
    assert diff_args.after == "new.png"
    assert diff_args.dimension_policy == "strict"
    assert diff_args.max_decoded_pixels == 40_000_000
    assert _is_machine_output(diff_args) is True

    git_args = _parse(
        "assets",
        "git",
        "--repo",
        ".",
        "--base",
        "main",
        "--head",
        "HEAD",
        "--dimension-policy",
        "pad",
        "--max-decoded-megapixels",
        "2.5",
    )
    assert git_args.func is _cmd_assets_git
    assert git_args.base == "main"
    assert git_args.head == "HEAD"
    assert git_args.dimension_policy == "pad"
    assert git_args.max_decoded_pixels == 2_500_000


def test_asset_terminal_output_surfaces_decoded_image_cost(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _emit_asset_payload(
        {
            "kind": "asset_diff",
            "summary": "Image changed.",
            "decoded_cost": {
                "before_pixels": 1_411_000,
                "after_pixels": 1_411_000,
                "max_decoded_pixels": 40_000_000,
            },
        },
        json_output=False,
    )

    captured = capsys.readouterr().out
    assert "Decoded image cost:" in captured
    assert "before=1411000 px" in captured
    assert "limit=40000000 px" in captured


def test_git_assets_compatibility_route_normalizes_before_argparse() -> None:
    assert _normalize_argv(["git", "assets", "--base", "main"]) == [
        "assets",
        "git",
        "--base",
        "main",
    ]


def test_github_pr_cli_emits_deterministic_review_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cmd_github_pr(argparse.Namespace(url="https://github.com/owner/repo/pull/42"))

    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "kind": "github_pr",
        "owner": "owner",
        "repo": "repo",
        "number": 42,
        "review_command": "intentdiff-github-app review-pr --owner owner --repo repo --number 42",
    }


def test_github_pr_cli_rejects_unsafe_non_github_urls() -> None:
    with pytest.raises(ValueError):
        _cmd_github_pr(argparse.Namespace(url="https://example.com/owner/repo/pull/42"))


def test_gist_diff_cli_emits_deterministic_review_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cmd_gist_diff(argparse.Namespace(url="https://gist.github.com/owner/abcdef#file-demo-py"))

    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "kind": "gist_diff",
        "gist_id": "abcdef",
        "revision": "file-demo-py",
        "review_command": "intentdiff gist-diff 'https://gist.github.com/owner/abcdef#file-demo-py' --format html",
    }


def test_gist_diff_cli_quotes_shell_sensitive_urls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cmd_gist_diff(argparse.Namespace(url="https://gist.github.com/owner/abc'def"))

    payload = json.loads(capsys.readouterr().out)

    assert payload["gist_id"] == "abc'def"
    assert payload["review_command"] == (
        "intentdiff gist-diff 'https://gist.github.com/owner/abc'\"'\"'def' --format html"
    )


def test_cli_banner_contains_branding() -> None:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=100)

    _render_cli_banner(console=console)

    output = stream.getvalue()
    assert "IntentDiff" in output
    assert "Semantic review shell" in output
    assert "Diff with meaning." in output


def test_terminal_render_uses_rich_summary_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from intentdiff import cli
    from intentdiff.core.models import Change, ChangeType

    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=100)
    monkeypatch.setattr(cli._shared, "_console", console)
    diff = SemanticDiff(
        old_filename="old.py",
        new_filename="new.py",
        language="python",
        has_semantic_changes=True,
        changes=[
            Change(
                change_type=ChangeType.MODIFICATION,
                description="Function body changed",
            )
        ],
    )

    _render(diff, "terminal", None)

    output = stream.getvalue()
    assert "Semantic diff" in output
    assert "Changes" in output
    assert "MODIFICATION" in output
    assert "Function body changed" in output


def test_cli_banner_gate_keeps_machine_output_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _should_show_cli_banner(_parse("git"), is_terminal=True) is True
    assert _should_show_cli_banner(_parse("--no-banner", "git"), is_terminal=True) is False
    assert _should_show_cli_banner(_parse("git", "--format", "json"), is_terminal=True) is False
    assert _should_show_cli_banner(_parse("git", "--output", "out.txt"), is_terminal=True) is False
    assert _should_show_cli_banner(_parse("cache", "export"), is_terminal=True) is False
    assert _should_show_cli_banner(_parse("live-server"), is_terminal=True) is False
    assert _should_show_cli_banner(_parse("git"), is_terminal=False) is False

    monkeypatch.setenv("INTENTDIFF_NO_BANNER", "1")
    assert _should_show_cli_banner(_parse("git"), is_terminal=True) is False


def test_shell_line_dispatches_through_existing_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = _build_parser()
    dispatched: list[argparse.Namespace] = []

    def fake_run(args: argparse.Namespace, *, show_banner: bool = True) -> None:
        dispatched.append(args)

    monkeypatch.setattr("intentdiff.cli._shared._run_parsed_command", fake_run)

    assert _run_shell_line("string old new --lang python", parser) is True

    assert len(dispatched) == 1
    assert dispatched[0].command == "string"
    assert dispatched[0].old == "old"
    assert dispatched[0].new == "new"
    assert dispatched[0].lang == "python"


def test_shell_builtins_and_errors_keep_loop_alive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = _build_parser()

    assert _run_shell_line("help git", parser) is True
    assert _run_shell_line('string "unterminated', parser) is True
    assert _run_shell_line("exit", parser) is False
    assert _run_shell_line("quit", parser) is False

    captured = capsys.readouterr()
    assert "usage: intentdiff git" in captured.out
    assert "Parse error" in captured.err


def test_shell_command_prints_full_banner_and_exits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    _cmd_shell(_parse("shell"))

    captured = capsys.readouterr()
    assert "IntentDiff" in captured.out
    assert "Semantic review shell" in captured.out
    assert "Diff with meaning." in captured.out


def test_pyproject_exposes_intentdiff_cli() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["intentdiff"] == "intentdiff.cli:main"
    assert set(scripts) == {"intentdiff"}


def test_profile_phases_flag_is_available_on_diff_commands() -> None:
    assert _parse("git", "--profile-phases").profile_phases is True
    assert _parse("file", "old.py", "new.py", "--profile-phases").profile_phases is True
    assert _parse("patch", "change.patch", "--profile-phases").profile_phases is True
    assert _parse("string", "old", "new", "--profile-phases").profile_phases is True


def test_emit_phase_profiles_writes_compact_stderr_json(capsys: pytest.CaptureFixture[str]) -> None:
    diff = SemanticDiff(
        old_filename="old.py",
        new_filename="new.py",
        language="python",
        metadata={
            "phase_timings": {
                "schema_version": 1,
                "total_ms": 1.25,
                "phases": [{"name": "parser_selection", "duration_ms": 0.5}],
            }
        },
    )

    _emit_phase_profiles(diff, render_ms=2.0)

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["cli_render_ms"] == 2.0
    assert payload["phase_profiles"][0]["new_filename"] == "new.py"
    assert payload["phase_profiles"][0]["phase_timings"]["phases"][0]["name"] == (
        "parser_selection"
    )


# ---------------------------------------------------------------------------
# Argument parsing — index subcommand
# ---------------------------------------------------------------------------


def test_index_defaults():
    ns = _parse("index")
    assert ns.repo == "."
    assert ns.ref == "HEAD"
    assert ns.cache_path == ".intentdiff-cache"
    assert ns.force is False


def test_index_custom_repo():
    ns = _parse("index", "/my/repo")
    assert ns.repo == "/my/repo"


def test_index_custom_ref():
    ns = _parse("index", "--ref", "v2.0")
    assert ns.ref == "v2.0"


def test_index_custom_cache_path():
    ns = _parse("index", "--cache-path", "/tmp/my-cache")
    assert ns.cache_path == "/tmp/my-cache"


def test_index_force_flag():
    ns = _parse("index", "--force")
    assert ns.force is True


def test_index_all_options():
    ns = _parse("index", "/repo", "--ref", "main", "--cache-path", "/c", "--force")
    assert ns.repo == "/repo"
    assert ns.ref == "main"
    assert ns.cache_path == "/c"
    assert ns.force is True


# ---------------------------------------------------------------------------
# Argument parsing — cache subcommands
# ---------------------------------------------------------------------------


def test_direct_diff_commands_parse_diagnostics_flag():
    git_ns = _parse("git", "--diagnostics")
    file_ns = _parse("file", "old.py", "new.py", "--diagnostics")
    patch_ns = _parse("patch", "--diagnostics")
    string_ns = _parse("string", "old", "new", "--diagnostics")

    assert git_ns.diagnostics is True
    assert file_ns.diagnostics is True
    assert patch_ns.diagnostics is True
    assert string_ns.diagnostics is True


def test_direct_diff_commands_parse_diagnostics_db_flag(tmp_path: Path) -> None:
    db = tmp_path / "diagnostics.duckdb"

    git_ns = _parse("git", "--diagnostics-db", str(db))
    file_ns = _parse("file", "old.py", "new.py", "--diagnostics-db", str(db))
    patch_ns = _parse("patch", "--diagnostics-db", str(db))
    string_ns = _parse("string", "old", "new", "--diagnostics-db", str(db))

    assert git_ns.diagnostics_db == str(db)
    assert file_ns.diagnostics_db == str(db)
    assert patch_ns.diagnostics_db == str(db)
    assert string_ns.diagnostics_db == str(db)


def test_diagnostics_commands_parse() -> None:
    summary = _parse("diagnostics", "summary", "--format", "json", "--limit", "3")
    hotspots = _parse("diagnostics", "hotspots", "--limit", "4")
    query = _parse("diagnostics", "query", "select * from diagnostic_runs")

    assert summary.func is _cmd_diagnostics_summary
    assert summary.db == ".intentdiff/diagnostics.duckdb"
    assert summary.limit == 3
    assert hotspots.func is _cmd_diagnostics_hotspots
    assert hotspots.limit == 4
    assert query.func is _cmd_diagnostics_query
    assert query.format == "json"


def test_diagnostics_json_output_is_machine_output() -> None:
    from intentdiff.cli import _is_machine_output

    assert _is_machine_output(_parse("diagnostics", "summary", "--format", "json")) is True
    assert _is_machine_output(_parse("diagnostics", "hotspots")) is False


def test_diagnostics_summary_uses_query_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def __enter__(self) -> "FakeStore":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def recent_diagnostic_runs(self, limit: int) -> list[dict[str, object]]:
            assert limit == 2
            return [{"id": "run-1", "peak_fuel": 25_000_000}]

        def fuel_by_language(self, limit: int) -> list[dict[str, object]]:
            assert limit == 2
            return [{"language": "typescript", "total_fuel": 25_000_000}]

    monkeypatch.setattr("intentdiff.cli._commands._diagnostics_store", lambda _args: FakeStore())

    _cmd_diagnostics_summary(
        argparse.Namespace(db="diagnostics.duckdb", limit=2, format="json", output=None)
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["recent_runs"][0]["id"] == "run-1"
    assert payload["fuel_by_language"][0]["language"] == "typescript"


def test_direct_diff_commands_parse_guardrails_strict_flag():
    git_ns = _parse("git", "--guardrails-strict")
    file_ns = _parse("file", "old.py", "new.py", "--guardrails-strict")
    patch_ns = _parse("patch", "--guardrails-strict")
    string_ns = _parse("string", "old", "new", "--guardrails-strict")

    assert git_ns.guardrails_strict is True
    assert file_ns.guardrails_strict is True
    assert patch_ns.guardrails_strict is True
    assert string_ns.guardrails_strict is True


def test_direct_diff_commands_parse_guardrail_report_flags(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    json_out = tmp_path / "guardrails.json"
    sarif_out = tmp_path / "guardrails.sarif"

    commands = [
        ("git",),
        ("file", "old.yaml", "new.yaml"),
        ("patch",),
        ("string", "old", "new"),
    ]
    for command in commands:
        ns = _parse(
            *command,
            "--guardrails-policy",
            str(policy),
            "--guardrails-annotations",
            "github",
            "--guardrails-json",
            str(json_out),
            "--guardrails-sarif",
            str(sarif_out),
        )

        assert ns.guardrails_policy == str(policy)
        assert ns.guardrails_annotations == "github"
        assert ns.guardrails_json == str(json_out)
        assert ns.guardrails_sarif == str(sarif_out)


def test_patch_command_uses_current_patch_source_interface(tmp_path: Path) -> None:
    patch_file = tmp_path / "change.patch"
    base_file = tmp_path / "query.sql"
    base_file.write_text("SELECT old_value;\n", encoding="utf-8")
    patch_file.write_text(
        "\n".join(
            [
                "--- a/query.sql",
                "+++ b/query.sql",
                "@@ -1 +1 @@",
                "-SELECT old_value;",
                "+SELECT new_value;",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ns = _parse("patch", str(patch_file), "--base", str(base_file), "--format", "json")
    diff = SemanticDiff(old_filename="query.sql", new_filename="query.sql", language="sql")
    mock_differ = _make_mock_differ()
    mock_differ._config.plugin_fuel = 123
    mock_differ.diff.return_value = diff

    with (
        patch("intentdiff.cli._commands._differ", return_value=mock_differ),
        patch("intentdiff.cli._shared._render") as render,
    ):
        _cmd_patch(ns)

    source = mock_differ.diff.call_args.args[0]
    old_content, new_content, filename, language_hint = source.get_content()
    assert old_content == "SELECT old_value;\n"
    assert new_content == "SELECT new_value;\n"
    assert filename == "query.sql"
    assert language_hint is None
    render.assert_called_once_with(diff, "json", None, fuel=123)


def test_guardrails_check_command_parses_flags(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    output = tmp_path / "guardrails.sarif"

    ns = _parse(
        "guardrails",
        "check",
        str(tmp_path),
        "--old",
        "origin/main",
        "--new",
        "HEAD",
        "--policy",
        str(policy),
        "--strict",
        "--fuel",
        "12345",
        "--format",
        "sarif",
        "--output",
        str(output),
        "--annotations",
        "github",
    )

    assert ns.repo == str(tmp_path)
    assert ns.old == "origin/main"
    assert ns.new == "HEAD"
    assert ns.guardrails_policy == str(policy)
    assert ns.strict is True
    assert ns.fuel == 12345
    assert ns.format == "sarif"
    assert ns.output == str(output)
    assert ns.annotations == "github"
    assert ns.func is _cmd_guardrails_check


def test_differ_helper_enables_diagnostics_config():
    differ = _differ(diagnostics=True)

    assert differ._config.diagnostics is True


def test_differ_helper_enables_guardrails_strict_config():
    differ = _differ(guardrails_strict=True)

    assert differ._config.guardrails_strict is True


def test_differ_helper_uses_guardrail_policy_override(tmp_path: Path) -> None:
    policy = tmp_path / "custom-policy.yaml"

    differ = _differ(guardrails_policy=policy)

    assert differ._config.guardrail_policy_path == policy


def test_guardrails_check_writes_json_report(tmp_path: Path) -> None:
    output = tmp_path / "guardrails.json"
    ns = _parse(
        "guardrails",
        "check",
        str(tmp_path),
        "--format",
        "json",
        "--output",
        str(output),
    )
    mock = MagicMock()
    mock.diff_commit.return_value = [_guardrail_diff()]

    with patch("intentdiff.cli._shared.SemanticDiffer", return_value=mock):
        _cmd_guardrails_check(ns)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["violation_count"] == 1
    assert payload["immutable_count"] == 1
    assert payload["passed"] is True


def test_guardrails_check_exits_two_for_strict_immutable(tmp_path: Path) -> None:
    output = tmp_path / "guardrails.sarif"
    ns = _parse(
        "guardrails",
        "check",
        str(tmp_path),
        "--strict",
        "--format",
        "sarif",
        "--output",
        str(output),
    )
    mock = MagicMock()
    mock.diff_commit.return_value = [_guardrail_diff()]

    with patch("intentdiff.cli._shared.SemanticDiffer", return_value=mock):
        with pytest.raises(SystemExit) as exc_info:
            _cmd_guardrails_check(ns)

    assert exc_info.value.code == 2
    assert json.loads(output.read_text(encoding="utf-8"))["version"] == "2.1.0"


def test_guardrails_check_fuel_overrides_intentdiff_yaml(tmp_path: Path) -> None:
    (tmp_path / "intentdiff.yaml").write_text(
        """
config:
  plugin_fuel: 111
""",
        encoding="utf-8",
    )
    ns = _parse(
        "guardrails",
        "check",
        str(tmp_path),
        "--fuel",
        "222",
    )
    mock = MagicMock()
    mock.diff_commit.return_value = []
    seen_configs = []

    def _factory(config):
        seen_configs.append(config)
        return mock

    with patch("intentdiff.cli._shared.SemanticDiffer", side_effect=_factory):
        _cmd_guardrails_check(ns)

    assert seen_configs[0].plugin_fuel == 222


def test_project_config_loads_from_intentdiff_yaml(tmp_path: Path) -> None:
    (tmp_path / "intentdiff.yaml").write_text(
        """
config:
  min_similarity: 0.7
  approx_move_threshold: 0.3
  min_height: 4
  ignore_style: false
  detect_refactorings: false
  strict_plugins: true
  plugin_fuel: 10_000_000
  max_cst_bytes: 4_194_304
""",
        encoding="utf-8",
    )

    cfg = load_project_diff_config(tmp_path)

    assert cfg.min_similarity == 0.7
    assert cfg.approx_move_threshold == 0.3
    assert cfg.min_height == 4
    assert cfg.ignore_style is False
    assert cfg.detect_refactorings is False
    assert cfg.strict_plugins is True
    assert cfg.plugin_fuel == 10_000_000
    assert cfg.max_cst_bytes == 4_194_304


def test_project_config_rejects_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "intentdiff.yaml").write_text(
        """
config:
  min_similarity: 0.7
  surprise_knob: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported key"):
        load_project_diff_config(tmp_path)


def test_differ_helper_uses_intentdiff_yaml_config(tmp_path: Path) -> None:
    (tmp_path / "intentdiff.yaml").write_text(
        """
config:
  plugin_fuel: 12_345_678
  min_similarity: 0.65
""",
        encoding="utf-8",
    )

    differ = _differ(config_start_path=tmp_path)

    assert differ._config.plugin_fuel == 12_345_678
    assert differ._config.min_similarity == 0.65


def test_differ_helper_cli_fuel_overrides_intentdiff_yaml(tmp_path: Path) -> None:
    (tmp_path / "intentdiff.yaml").write_text(
        """
config:
  plugin_fuel: 12_345_678
""",
        encoding="utf-8",
    )

    differ = _differ(fuel=99_000_000, config_start_path=tmp_path)

    assert differ._config.plugin_fuel == 99_000_000


def test_cache_stats_defaults():
    ns = _parse("cache", "stats")
    assert ns.cache_path == ".intentdiff-cache"
    assert ns.func.__name__ == "_cmd_cache_stats"


def test_cache_stats_custom_path():
    ns = _parse("cache", "stats", "--cache-path", "/tmp/c")
    assert ns.cache_path == "/tmp/c"


def test_cache_clear_defaults():
    ns = _parse("cache", "clear")
    assert ns.parse is False
    assert ns.diff is False
    assert ns.index is False
    assert ns.all is False


def test_cache_clear_parse_flag():
    ns = _parse("cache", "clear", "--parse")
    assert ns.parse is True
    assert ns.diff is False
    assert ns.index is False


def test_cache_clear_all_flag():
    ns = _parse("cache", "clear", "--all")
    assert ns.all is True


def test_cache_clear_combined_flags():
    ns = _parse("cache", "clear", "--parse", "--diff")
    assert ns.parse is True
    assert ns.diff is True
    assert ns.index is False


# ---------------------------------------------------------------------------
# _cmd_index — success paths
# ---------------------------------------------------------------------------


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_success(MockIndexer, MockDiffer, tmp_path):
    MockDiffer.return_value = _make_mock_differ()
    MockIndexer.return_value.index_repo.return_value = _make_result(
        files_indexed=10, files_skipped=3
    )
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    _cmd_index(args)  # should not raise
    call_args = MockIndexer.return_value.index_repo.call_args
    assert call_args[0][0] == str(tmp_path)
    assert call_args[1]["ref"] == "HEAD"
    assert call_args[1]["force"] is False
    assert callable(call_args[1]["on_progress"])


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_from_cache(MockIndexer, MockDiffer, tmp_path):
    MockDiffer.return_value = _make_mock_differ()
    MockIndexer.return_value.index_repo.return_value = _make_result(from_cache=True)
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    _cmd_index(args)  # should not raise


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_with_few_errors(MockIndexer, MockDiffer, tmp_path):
    errors = [(f"file_{i}.py", f"error {i}") for i in range(5)]
    MockDiffer.return_value = _make_mock_differ()
    MockIndexer.return_value.index_repo.return_value = _make_result(
        files_indexed=0, errors=errors
    )
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    _cmd_index(args)  # parse errors are shown but do not raise


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_with_many_errors_truncates(MockIndexer, MockDiffer, tmp_path):
    errors = [(f"file_{i}.py", f"error {i}") for i in range(15)]
    MockDiffer.return_value = _make_mock_differ()
    MockIndexer.return_value.index_repo.return_value = _make_result(
        files_indexed=0, errors=errors
    )
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    _cmd_index(args)  # "…and 5 more" is printed; should not raise


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_force_passed_through(MockIndexer, MockDiffer, tmp_path):
    MockDiffer.return_value = _make_mock_differ()
    MockIndexer.return_value.index_repo.return_value = _make_result()
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=True, cache_path=str(tmp_path / "cache")
    )
    _cmd_index(args)
    _, kwargs = MockIndexer.return_value.index_repo.call_args
    assert kwargs["force"] is True


# ---------------------------------------------------------------------------
# _cmd_index — error / cleanup paths
# ---------------------------------------------------------------------------


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_exception_exits_1(MockIndexer, MockDiffer, tmp_path):
    MockDiffer.return_value = _make_mock_differ()
    MockIndexer.return_value.index_repo.side_effect = RuntimeError("git not found")
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    with pytest.raises(SystemExit) as exc_info:
        _cmd_index(args)
    assert exc_info.value.code == 1


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_cache_closed_on_success(MockIndexer, MockDiffer, tmp_path):
    mock_cache = MagicMock()
    mock_differ = _make_mock_differ()
    mock_differ._cache = mock_cache
    MockDiffer.return_value = mock_differ
    MockIndexer.return_value.index_repo.return_value = _make_result()
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    _cmd_index(args)
    mock_cache.close.assert_called_once()


@patch("intentdiff.cli._commands.SemanticDiffer")
@patch("intentdiff.core.indexer.Indexer")
def test_cmd_index_cache_closed_on_error(MockIndexer, MockDiffer, tmp_path):
    """Cache must be closed even when indexing raises."""
    mock_cache = MagicMock()
    mock_differ = _make_mock_differ()
    mock_differ._cache = mock_cache
    MockDiffer.return_value = mock_differ
    MockIndexer.return_value.index_repo.side_effect = RuntimeError("boom")
    args = argparse.Namespace(
        repo=str(tmp_path), ref="HEAD", force=False, cache_path=str(tmp_path / "cache")
    )
    with pytest.raises(SystemExit):
        _cmd_index(args)
    mock_cache.close.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_cache_stats
# ---------------------------------------------------------------------------


def test_cache_stats_missing_db(tmp_path):
    args = argparse.Namespace(cache_path=str(tmp_path))
    _cmd_cache_stats(args)  # graceful — just prints a message, no exception


def test_cache_stats_with_data(tmp_path):
    from intentdiff.cache.sqlite_store import SqliteCacheStore

    db = tmp_path / "cache.db"
    store = SqliteCacheStore(db)
    store.put_parse("key1", '{"id": "n1"}', grammar_id="python")
    store.close()

    args = argparse.Namespace(cache_path=str(tmp_path))
    _cmd_cache_stats(args)  # should print table without raising


def test_cache_stats_via_main_missing_db(tmp_path):
    rc = _run("cache", "stats", "--cache-path", str(tmp_path))
    assert rc == 0


# ---------------------------------------------------------------------------
# _cmd_cache_clear
# ---------------------------------------------------------------------------


def test_cache_clear_no_flags_exits_1(tmp_path):
    db = tmp_path / "cache.db"
    from intentdiff.cache.sqlite_store import SqliteCacheStore
    SqliteCacheStore(db).close()

    args = argparse.Namespace(
        cache_path=str(tmp_path), parse=False, diff=False, index=False, all=False
    )
    with pytest.raises(SystemExit) as exc_info:
        _cmd_cache_clear(args)
    assert exc_info.value.code == 1


def test_cache_clear_missing_db(tmp_path):
    args = argparse.Namespace(cache_path=str(tmp_path), parse=True, diff=True, index=True, all=True)
    _cmd_cache_clear(args)  # graceful — prints yellow message, no exception


def test_cache_clear_all_removes_entries(tmp_path):
    from intentdiff.cache.sqlite_store import SqliteCacheStore

    db = tmp_path / "cache.db"
    store = SqliteCacheStore(db)
    store.put_parse("k1", '{}', grammar_id="python")
    store.put_symbol_index("k2", "{}", "{}")
    store.close()

    args = argparse.Namespace(
        cache_path=str(tmp_path), parse=False, diff=False, index=False, all=True
    )
    _cmd_cache_clear(args)

    store2 = SqliteCacheStore(db)
    stats = store2.stats()
    store2.close()
    assert stats["parse_cache"]["count"] == 0
    assert stats["symbol_index_cache"]["count"] == 0


def test_cache_clear_parse_only_leaves_index(tmp_path):
    from intentdiff.cache.sqlite_store import SqliteCacheStore

    db = tmp_path / "cache.db"
    store = SqliteCacheStore(db)
    store.put_parse("k1", '{}', grammar_id="python")
    store.put_symbol_index("k2", "{}", "{}")
    store.close()

    args = argparse.Namespace(
        cache_path=str(tmp_path), parse=True, diff=False, index=False, all=False
    )
    _cmd_cache_clear(args)

    store2 = SqliteCacheStore(db)
    stats = store2.stats()
    store2.close()
    assert stats["parse_cache"]["count"] == 0
    assert stats["symbol_index_cache"]["count"] == 1  # untouched


def test_cache_clear_via_main_all(tmp_path):
    from intentdiff.cache.sqlite_store import SqliteCacheStore

    db = tmp_path / "cache.db"
    SqliteCacheStore(db).close()

    rc = _run("cache", "clear", "--all", "--cache-path", str(tmp_path))
    assert rc == 0


def test_cache_clear_via_main_no_flags(tmp_path):
    from intentdiff.cache.sqlite_store import SqliteCacheStore

    db = tmp_path / "cache.db"
    SqliteCacheStore(db).close()

    rc = _run("cache", "clear", "--cache-path", str(tmp_path))
    assert rc == 1
