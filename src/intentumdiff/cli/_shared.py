"""
intentumdiff.cli
~~~~~~~~~~~~~~~~~~~~~

Command-line interface for IntentumDiff.

Usage
-----
    intentumdiff git <repo> <file> [--old REF] [--new REF] [--format FORMAT] [--output FILE]
    intentumdiff file <old-file> <new-file>   [--format FORMAT] [--output FILE]
    intentumdiff patch [PATCH_FILE]            [--base BASE_FILE]
    intentumdiff string <old> <new>            [--lang LANG]
    intentumdiff plugins                       List installed parser and renderer plugins

Formats: terminal (default), terminal-color, json, patch, html, llm
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

try:  # rich-click is a runtime dependency, but this fallback keeps source-tree tests robust.
    import rich_click as click
    _CLICK_RUNTIME_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal dev envs.
    _CLICK_RUNTIME_AVAILABLE = False
    click = None  # type: ignore[assignment]

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from intentumdiff import (
    DiffConfig,
    GuardrailCheckResult,
    GuardrailSeverity,
    SemanticDiff,
    SemanticDiffer,
    __version__,
)
from intentumdiff.analysis.guardrail_reports import (
    guardrail_result_from_diffs,
    render_guardrail_annotations,
    render_guardrail_json,
    render_guardrail_sarif,
)
from intentumdiff.core.config import load_project_diff_config

_console = Console()
_err = Console(stderr=True, highlight=False)

_NO_BANNER_ENV = "INTENTUMDIFF_NO_BANNER"
_MACHINE_PROTOCOL_COMMANDS = {"live-server", "lsp-server"}
_MACHINE_OUTPUT_FORMATS = {"json", "patch", "html", "llm", "sarif"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _differ(
    fuel: int | None = None,
    resolve_references: bool = False,
    parallel: int | bool | None = None,
    diagnostics: bool = False,
    profile_phases: bool = False,
    guardrails_strict: bool = False,
    config_start_path: str | Path | None = None,
    guardrails_policy: str | Path | None = None,
) -> SemanticDiffer:
    cfg = _load_cli_config(fuel=fuel, config_start_path=config_start_path)
    cfg.resolve_references = resolve_references
    cfg.diagnostics = diagnostics
    if profile_phases:
        cfg.profile_phases = True
    cfg.guardrails_strict = guardrails_strict
    if guardrails_policy is not None:
        cfg.guardrail_policy_path = Path(guardrails_policy)
    if parallel is not None:
        cfg.parallel = parallel
    return SemanticDiffer(cfg)


def _load_cli_config(
    *,
    fuel: int | None = None,
    config_start_path: str | Path | None = None,
) -> DiffConfig:
    cfg = load_project_diff_config(config_start_path)
    if fuel is not None:
        cfg.plugin_fuel = fuel
    _warn_if_unlimited_fuel(cfg.plugin_fuel)
    return cfg


def _banner_disabled_by_env() -> bool:
    value = os.environ.get(_NO_BANNER_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _render_cli_banner(
    *,
    compact: bool = False,
    console: Console | None = None,
) -> None:
    """Render the IntentumDiff banner for interactive CLI sessions."""
    out = console or _console
    title = Text("IntentumDiff", style="bold cyan")
    body = Text.assemble(
        ("Semantic review shell", "bold yellow"),
        ("  ", "dim"),
        (f"v{__version__}", "dim"),
        "\n",
        ("Diff with meaning. Moves, intent, guardrails, and review signals.", "dim"),
    )
    if compact:
        out.print(Panel(body, title=title, border_style="cyan", box=box.ROUNDED, expand=False))
        return

    out.print(
        Panel(
            body,
            title=title,
            subtitle=Text("type help for commands", style="dim"),
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
            padding=(1, 2),
        )
    )
    out.print()


def _version_text() -> str:
    return f"IntentumDiff {__version__}"


def _is_machine_output(args: argparse.Namespace) -> bool:
    if getattr(args, "command", None) in _MACHINE_PROTOCOL_COMMANDS:
        return True
    if getattr(args, "format", "terminal") in _MACHINE_OUTPUT_FORMATS:
        return True
    if getattr(args, "command", None) == "assets" and getattr(args, "json", False):
        return True
    if getattr(args, "output", None):
        return True
    if getattr(args, "command", None) == "cache" and getattr(
        args,
        "cache_command",
        None,
    ) == "export":
        return True
    return (
        getattr(args, "command", None) == "diagnostics"
        and getattr(args, "format", "terminal") == "json"
        or getattr(args, "command", None) == "watch"
        and getattr(args, "live_stdin", False)
    )


def _should_show_cli_banner(
    args: argparse.Namespace,
    *,
    is_terminal: bool | None = None,
) -> bool:
    if getattr(args, "no_banner", False) or _banner_disabled_by_env():
        return False
    if getattr(args, "command", None) == "shell":
        return False
    if _is_machine_output(args):
        return False
    terminal = _console.is_terminal if is_terminal is None else is_terminal
    return bool(terminal)


def _maybe_render_cli_banner(args: argparse.Namespace) -> None:
    if _should_show_cli_banner(args):
        _render_cli_banner(compact=True)


def _run_parsed_command(
    args: argparse.Namespace,
    *,
    show_banner: bool = True,
) -> None:
    if show_banner:
        _maybe_render_cli_banner(args)
    args.func(args)


def _print_shell_help(parser: argparse.ArgumentParser, words: list[str]) -> None:
    if not words:
        parser.print_help()
        return
    try:
        parser.parse_args([*words, "--help"])
    except SystemExit:
        return


def _run_shell_line(line: str, parser: argparse.ArgumentParser) -> bool:
    """Run one shell line. Return False when the shell should exit."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped in {"exit", "quit"}:
        return False
    if stripped == "version":
        _console.print(_version_text())
        return True

    try:
        words = shlex.split(stripped)
    except ValueError as exc:
        _err.print(f"[red]Parse error:[/red] {exc}")
        return True
    if not words:
        return True
    if words[0] == "help":
        _print_shell_help(parser, words[1:])
        return True

    try:
        args = parser.parse_args(words)
    except SystemExit:
        return True
    if getattr(args, "command", None) == "shell":
        _err.print("[yellow]Already inside the IntentumDiff shell.[/yellow]")
        return True
    try:
        args.no_banner = True
        _run_parsed_command(args, show_banner=False)
    except KeyboardInterrupt:
        _err.print("\n[dim]Command interrupted.[/dim]")
    except Exception as exc:  # noqa: BLE001
        _err.print(f"[red]Error:[/red] {exc}")
    return True


def _cmd_shell(args: argparse.Namespace) -> None:
    """Start a lightweight interactive IntentumDiff command shell."""
    if not getattr(args, "no_banner", False) and not _banner_disabled_by_env():
        _render_cli_banner(compact=False)
    _console.print("[dim]Type 'help' for commands, 'exit' or 'quit' to leave.[/dim]")
    from intentumdiff.cli._parser import _build_parser

    parser = _build_parser()
    while True:
        try:
            line = input("intentumdiff> ")
        except EOFError:
            _console.print()
            return
        except KeyboardInterrupt:
            _console.print()
            continue
        if not _run_shell_line(line, parser):
            return


def _write_output(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text)


def _json_default(value: object) -> str:
    return str(value)


def _record_diagnostics_db(
    diffs: SemanticDiff | list[SemanticDiff],
    args: argparse.Namespace,
) -> None:
    db_path = getattr(args, "diagnostics_db", None)
    if not db_path:
        return
    from intentumdiff.cache.duckdb_store import DuckDBAnalyticsStore

    items = diffs if isinstance(diffs, list) else [diffs]
    with DuckDBAnalyticsStore(db_path) as store:
        store.record_diagnostics_run(
            [item.model_dump() for item in items],
            command=getattr(args, "command", ""),
            repo=str(getattr(args, "repo", "")),
            argv=getattr(args, "_argv", None),
        )


def _render_terminal(diff: SemanticDiff) -> None:
    if diff.guardrail_violations:
        guardrail_table = Table(
            title="Protected semantic changes",
            title_style="bold red",
            box=box.SIMPLE_HEAVY,
            border_style="red",
        )
        guardrail_table.add_column("Severity", style="bold", no_wrap=True)
        guardrail_table.add_column("Location", style="cyan")
        guardrail_table.add_column("Message")
        guardrail_table.add_column("Value", style="dim")
        for violation in diff.guardrail_violations:
            color = "red" if violation.severity == GuardrailSeverity.IMMUTABLE else "yellow"
            old_new = ""
            if violation.old_value or violation.new_value:
                old_new = f"{violation.old_value!r} -> {violation.new_value!r}"
            guardrail_table.add_row(
                f"[{color}]{violation.severity.value.upper()}[/{color}]",
                f"{violation.file}::{violation.semantic_path}",
                violation.message,
                old_new,
            )
        _console.print(guardrail_table)
        _console.print()

    if not diff.has_semantic_changes:
        state_style = "green"
        state_title = "No changes detected"
        state_message = "IntentumDiff found no semantic differences."
        if diff.is_style_only:
            state_style = "yellow"
            state_title = "Style-only change"
            state_message = "Formatting changed, but no semantic differences were found."
        elif diff.is_fallback:
            state_style = "yellow"
            state_title = "Token fallback used"
            state_message = "Parse errors were detected, so IntentumDiff used token-level fallback."
        _console.print(
            Panel(
                state_message,
                title=f"[bold {state_style}]{state_title}[/bold {state_style}]",
                border_style=state_style,
                box=box.ROUNDED,
                expand=False,
            )
        )
        return
    scope_label = (diff.staging_status or "working tree").replace("_", " ")
    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold")
    header.add_column()
    header.add_row("Old", diff.old_filename or "<unknown>")
    header.add_row("New", diff.new_filename or "<unknown>")
    header.add_row("Language", diff.language or "unknown")
    header.add_row("Scope", scope_label)
    header.add_row("Changes", str(len(diff.changes)))
    _console.print(
        Panel(
            header,
            title="[bold cyan]Semantic diff[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )
    _console.print()

    changes_table = Table(
        box=box.SIMPLE,
        border_style="dim",
        header_style="bold",
        show_header=True,
    )
    changes_table.add_column("Type", no_wrap=True)
    changes_table.add_column("Description", overflow="fold")
    for change in diff.changes:
        from intentumdiff.core.models import ChangeType
        ct = (
            change.change_type.value
            if isinstance(change.change_type, ChangeType)
            else str(change.change_type)
        )
        color_map = {
            "ADDITION": "green",
            "DELETION": "red",
            "MODIFICATION": "yellow",
            "MOVE": "cyan",
            "REFACTORING": "magenta",
            "STYLE_ONLY": "dim",
        }
        color = color_map.get(ct, "white")
        changes_table.add_row(f"[{color}]{ct}[/{color}]", change.description)
    _console.print(changes_table)
    return

def _render(diff: SemanticDiff, fmt: str, output: str | None, fuel: int | None = None) -> None:
    if fmt == "terminal" and not output:
        _render_terminal(diff)
        return

    if fmt == "terminal":
        # Render to plain text file (no ANSI)
        with open(output, "w", encoding="utf-8") as fh:
            plain = Console(file=fh, highlight=False)
            plain.print(
                f"Semantic diff: {diff.old_filename} -> "
                f"{diff.new_filename} ({diff.language})"
            )
            for violation in diff.guardrail_violations:
                plain.print(
                    f"  GUARDRAIL      {violation.severity.value.upper()} "
                    f"{violation.file}::{violation.semantic_path} {violation.message}"
                )
            for change in diff.changes:
                plain.print(f"  {change.change_type.value:14} {change.description}")
        return

    if fmt == "json":
        _write_output(diff.model_dump_json(indent=2), output)
        return

    # For patch / html / llm — delegate to the matching Wasm renderer plugin
    from intentumdiff.plugins.loader import load_plugin

    wasm_dir = Path(__file__).parent / "wasm"
    candidates = list(wasm_dir.glob(f"{fmt.replace('-', '_')}_renderer.wasm"))
    if not candidates:
        # Fallback: scan for any renderer plugin that reports this format name
        for p in sorted(wasm_dir.glob("*_renderer.wasm")):
            with suppress(Exception):
                pl = load_plugin(p)
                if pl.call_format_name() == fmt:
                    candidates = [p]
                    break

    if not candidates:
        _err.print(f"[red]No renderer plugin found for format '{fmt}'.[/red]")
        sys.exit(1)

    try:
        diff_json = diff.model_dump_json()
        plugin_fuel = fuel if fuel is not None else 100_000_000
        adaptive_fuel = max(plugin_fuel, 20_000_000 + len(diff_json) * 100)
        pl = load_plugin(candidates[0], adaptive_fuel)
        result = pl.call_render(diff_json)
        _write_output(result, output)
    except Exception as exc:
        _err.print(f"[red]Renderer error: {exc}[/red]")
        sys.exit(1)


def _emit_phase_profiles(
    diffs: SemanticDiff | list[SemanticDiff],
    *,
    render_ms: float | None = None,
) -> None:
    items = diffs if isinstance(diffs, list) else [diffs]
    files: list[dict[str, Any]] = []
    for diff in items:
        phase_timings = diff.metadata.get("phase_timings")
        if phase_timings:
            files.append(
                {
                    "old_filename": diff.old_filename,
                    "new_filename": diff.new_filename,
                    "language": diff.language,
                    "phase_timings": phase_timings,
                }
            )
    if not files:
        return
    payload: dict[str, Any] = {"phase_profiles": files}
    if render_ms is not None:
        payload["cli_render_ms"] = round(render_ms, 3)
    print(json.dumps(payload, separators=(",", ":")), file=sys.stderr)


def _render_with_profile(
    diff: SemanticDiff,
    args: argparse.Namespace,
    *,
    fuel: int | None = None,
) -> None:
    started = time.perf_counter()
    _render(diff, args.format, args.output, fuel=fuel)
    _emit_phase_profiles(diff, render_ms=(time.perf_counter() - started) * 1000)


def _has_immutable_guardrail(diff: SemanticDiff) -> bool:
    return any(
        violation.severity == GuardrailSeverity.IMMUTABLE
        for violation in diff.guardrail_violations
    )


def _exit_if_guardrails_strict(diffs: SemanticDiff | list[SemanticDiff], strict: bool) -> None:
    result = _guardrail_result(diffs, strict=strict)
    _exit_if_guardrail_result_fails(result)


def _guardrail_result(
    diffs: SemanticDiff | list[SemanticDiff],
    *,
    strict: bool,
    checked_files: int | None = None,
) -> GuardrailCheckResult:
    return guardrail_result_from_diffs(
        diffs,
        strict=strict,
        checked_files=checked_files,
    )


def _emit_guardrail_reports(
    diffs: SemanticDiff | list[SemanticDiff],
    args: argparse.Namespace,
    *,
    checked_files: int | None = None,
) -> GuardrailCheckResult:
    result = _guardrail_result(
        diffs,
        strict=getattr(args, "guardrails_strict", False),
        checked_files=checked_files,
    )
    if getattr(args, "guardrails_annotations", "none") == "github":
        annotations = render_guardrail_annotations(result)
        if annotations:
            print(annotations)

    json_output = getattr(args, "guardrails_json", None)
    if json_output:
        _write_output(render_guardrail_json(result), json_output)

    sarif_output = getattr(args, "guardrails_sarif", None)
    if sarif_output:
        _write_output(render_guardrail_sarif(result), sarif_output)

    return result


def _exit_if_guardrail_result_fails(result: GuardrailCheckResult) -> None:
    if result.passed:
        return
    _err.print("[bold red]Immutable semantic guardrail violation detected.[/bold red]")
    sys.exit(2)


def _resolve_repo_root(start_path: str | Path) -> Path:
    """Resolve the git repository root for editor/live-server commands."""
    from intentumdiff.vcs.git_cli import NotAGitRepositoryError, resolve_repo_root

    start = Path(start_path)
    with suppress(NotAGitRepositoryError):
        return Path(resolve_repo_root(start)).resolve()
    return (start if start.is_dir() else start.parent).resolve()


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _asset_options_from_args(args: argparse.Namespace) -> Any:
    return {
        "dimension_policy": args.dimension_policy,
        "pixel_threshold": args.pixel_threshold,
        "region_min_area": args.region_min_area,
        "alpha_handling": args.alpha_handling,
        "max_decoded_pixels": args.max_decoded_pixels,
        "file_path": getattr(args, "file_path", None),
    }


def _print_asset_result(result: dict[str, Any]) -> None:
    _console.print(f"[bold cyan]{result.get('summary', 'Asset diff complete.')}[/bold cyan]")
    artifacts = result.get("artifacts") or {}
    if artifacts:
        for name, path in artifacts.items():
            _console.print(f"  [dim]{name}[/dim] {path}")
    warnings = result.get("warnings") or []
    for warning in warnings:
        _console.print(f"  [yellow]Warning:[/yellow] {warning}")
    decoded_cost = result.get("decoded_cost") or {}
    if decoded_cost:
        before_pixels = decoded_cost.get("before_pixels")
        after_pixels = decoded_cost.get("after_pixels")
        max_pixels = decoded_cost.get("max_decoded_pixels")
        _console.print(
            "  [dim]Decoded image cost:[/dim] "
            f"before={before_pixels} px after={after_pixels} px limit={max_pixels} px"
        )


def _emit_asset_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if payload.get("kind") == "asset_diff_batch":
        diffs = payload.get("asset_diffs") or []
        skipped = payload.get("skipped") or []
        if not diffs and not skipped:
            _console.print("[green]No changed image assets found.[/green]")
            return
        for item in diffs:
            path = item.get("file_path") or "<asset>"
            _console.rule(path)
            _print_asset_result(item)
        for item in skipped:
            _console.print(
                f"[yellow]Skipped:[/yellow] {item.get('file_path', '<asset>')} "
                f"({item.get('reason', 'no comparable image pair')})"
            )
        return
    _print_asset_result(payload)


def _parse_fuel(value: str) -> int:
    """argparse ``type=`` for --fuel: accepts integers or 'inf'/'infinite'/-1."""
    if value.strip().lower() in ("inf", "infinite", "unlimited"):
        return -1
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid fuel value {value!r}: use a positive integer, -1, 'inf', or 'infinite'"
        ) from exc
    if n == -1:
        return -1
    if n < 1:
        raise argparse.ArgumentTypeError(
            f"fuel must be a positive integer (or -1 / 'inf' for unlimited), got {n}"
        )
    return n


def _warn_if_unlimited_fuel(fuel: int | None) -> None:
    """Print a visible warning if the user opted into unlimited fuel."""
    if fuel == -1:
        _err.print(
            "[yellow]Warning: unlimited Wasm fuel — plugins are uncapped and may "
            "run indefinitely on malformed input.[/yellow]"
        )
