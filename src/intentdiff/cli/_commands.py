"""
intentdiff.cli
~~~~~~~~~~~~~~~~~~~~~

Command-line interface for IntentDiff.

Usage
-----
    intentdiff git <repo> <file> [--old REF] [--new REF] [--format FORMAT] [--output FILE]
    intentdiff file <old-file> <new-file>   [--format FORMAT] [--output FILE]
    intentdiff patch [PATCH_FILE]            [--base BASE_FILE]
    intentdiff string <old> <new>            [--lang LANG]
    intentdiff plugins                       List installed parser and renderer plugins

Formats: terminal (default), terminal-color, json, patch, html, llm
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:  # rich-click is a runtime dependency, but this fallback keeps source-tree tests robust.
    import rich_click as click
    _CLICK_RUNTIME_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal dev envs.
    _CLICK_RUNTIME_AVAILABLE = False
    click = None  # type: ignore[assignment]

from rich.panel import Panel
from rich.table import Table

from intentdiff import (
    FileSource,
    GitSource,
    PatchSource,
    SemanticDiff,
    SemanticDiffer,
    WorkingTreeSource,
)
from intentdiff.analysis.guardrail_reports import (
    render_guardrail_annotations,
    render_guardrail_json,
    render_guardrail_sarif,
    render_guardrail_terminal,
)
from intentdiff.cli._shared import (  # noqa: F401
    _asset_options_from_args,
    _console,
    _differ,
    _emit_asset_payload,
    _emit_guardrail_reports,
    _err,
    _exit_if_guardrail_result_fails,
    _guardrail_result,
    _json_default,
    _load_cli_config,
    _parse_fuel,
    _record_diagnostics_db,
    _render,
    _render_with_profile,
    _resolve_repo_root,
    _warn_if_unlimited_fuel,
    _write_output,
)


def _cmd_assets_diff(args: argparse.Namespace) -> None:
    from intentdiff.assets import diff_image_assets

    result = diff_image_assets(
        before_path=args.before,
        after_path=args.after,
        out_dir=args.out,
        options=_asset_options_from_args(args),
    )
    _emit_asset_payload(result, json_output=args.json)


def _cmd_assets_git(args: argparse.Namespace) -> None:
    from intentdiff.assets import diff_git_assets

    repo_path = args.repo
    new_ref: str = args.head
    if getattr(args, "staged", False):
        new_ref = ":staged"
    elif getattr(args, "unpushed", False):
        new_ref = ":unpushed"
    old_ref: str = args.base
    if old_ref is None:
        old_ref = "HEAD" if not new_ref or new_ref in {":staged", ":unpushed"} else "HEAD~1"

    payload = diff_git_assets(
        repo_path=repo_path,
        base=old_ref,
        head=new_ref,
        out_dir=args.out,
        options=_asset_options_from_args(args),
    )
    _emit_asset_payload(payload, json_output=args.json)


def _cmd_git(args: argparse.Namespace) -> None:
    from intentdiff.sources.git_source import _REF_STAGED, _REF_UNPUSHED
    from intentdiff.vcs.git_cli import NotAGitRepositoryError

    repo_path = args.repo  # '.' when not supplied

    # Resolve the effective new_ref from mutually-exclusive scope flags.
    new_ref: str = args.new  # may be "", ":staged", ":unpushed", or an explicit ref
    if getattr(args, "staged", False):
        new_ref = _REF_STAGED
    elif getattr(args, "unpushed", False):
        new_ref = _REF_UNPUSHED

    # Resolve the effective new_ref from mutually-exclusive scope flags.
    new_ref: str = args.new  # may be "", ":staged", ":unpushed", or an explicit ref
    if getattr(args, "staged", False):
        new_ref = _REF_STAGED
    elif getattr(args, "unpushed", False):
        new_ref = _REF_UNPUSHED

    # Smart --old default: HEAD for working-tree / scoped modes; HEAD~1 when an
    # explicit commit ref is given for --new so that `intentdiff git --new HEAD`
    # means "show the last commit" rather than HEAD vs HEAD = no diff.
    _is_working_tree_mode = not new_ref or new_ref in (_REF_STAGED, _REF_UNPUSHED)
    old_ref: str = args.old if args.old is not None else (
        "HEAD" if _is_working_tree_mode else "HEAD~1"
    )

    try:
        if args.file:
            # Single-file mode — pick the appropriate Source based on scope.
            if not new_ref:
                source: object = WorkingTreeSource(
                    repo_path=repo_path,
                    file_path=args.file,
                    ref=old_ref,
                )
            elif new_ref == _REF_STAGED:
                # Staged single-file: use WorkingTreeSource pointing at the index.
                # WorkingTreeSource reads the committed version for old_content and
                # the on-disk file for new_content — for staged-only accuracy the
                # user should rely on commit-wide mode or pass an explicit --new ref.
                source = WorkingTreeSource(
                    repo_path=repo_path,
                    file_path=args.file,
                    ref=old_ref,
                )
            else:
                source = GitSource(
                    repo_path=repo_path,
                    file_path=args.file,
                    old_ref=old_ref,
                    new_ref=new_ref,
                )
            differ = _differ(
                args.fuel,
                args.resolve_references,
                diagnostics=getattr(args, "diagnostics", False),
                profile_phases=getattr(args, "profile_phases", False),
                guardrails_strict=getattr(args, "guardrails_strict", False),
                config_start_path=repo_path,
                guardrails_policy=getattr(args, "guardrails_policy", None),
            )
            diff = differ.diff(source)
            _render_with_profile(diff, args, fuel=differ._config.plugin_fuel)
            result = _emit_guardrail_reports(diff, args)
            _record_diagnostics_db(diff, args)
            _exit_if_guardrail_result_fails(result)
        else:
            # Commit-wide mode — diff every changed file
            differ = _differ(
                args.fuel,
                args.resolve_references,
                parallel=args.parallel,
                diagnostics=getattr(args, "diagnostics", False),
                profile_phases=getattr(args, "profile_phases", False),
                guardrails_strict=getattr(args, "guardrails_strict", False),
                config_start_path=repo_path,
                guardrails_policy=getattr(args, "guardrails_policy", None),
            )
            diffs = differ.diff_commit(
                repo_path=repo_path,
                old_ref=old_ref,
                new_ref=new_ref,
            )
            if not diffs:
                _console.print("[green]No parseable changes found.[/green]")
                return
            for diff in diffs:
                if args.format == "terminal" and not args.output:
                    label = diff.staging_status or ""
                    rule_title = diff.new_filename or diff.old_filename
                    if label:
                        rule_title = f"{rule_title} [{label}]"
                    _console.rule(rule_title)
                _render_with_profile(diff, args, fuel=differ._config.plugin_fuel)
            result = _emit_guardrail_reports(diffs, args)
            _record_diagnostics_db(diffs, args)
            _exit_if_guardrail_result_fails(result)
    except NotAGitRepositoryError:
        # Distinguish a missing path (often a mistyped ref) from a real directory
        # that isn't a git repo — preserving the two GitPython-era messages + hint.
        if repo_path != "." and not Path(repo_path).exists():
            _err.print(f"[red]Repository path not found:[/red] {repo_path}")
            # When the repo path looks like a git ref name (e.g. "main", "HEAD",
            # "new") the user probably typed '-- ref' instead of '--new ref'.
            if not Path(repo_path).is_absolute():
                import re as _re
                if _re.fullmatch(r"[A-Za-z0-9_/.\-]+", repo_path):
                    _err.print(
                        f"[yellow]Hint: '{repo_path}' looks like a git ref. "
                        f"Did you mean [bold]--new {repo_path}[/bold]?[/yellow]"
                    )
        else:
            _err.print(
                f"[red]Not a git repository:[/red] {repo_path!r}  "
                "(pass a REPO path or run from inside a git repository)"
            )
        sys.exit(1)
    except ValueError as exc:
        # e.g. no tracking branch for --unpushed
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


def _cmd_file(args: argparse.Namespace) -> None:
    source = FileSource(old_path=args.old_file, new_path=args.new_file)
    differ = _differ(
        args.fuel,
        args.resolve_references,
        diagnostics=getattr(args, "diagnostics", False),
        profile_phases=getattr(args, "profile_phases", False),
        guardrails_strict=getattr(args, "guardrails_strict", False),
        config_start_path=args.new_file,
        guardrails_policy=getattr(args, "guardrails_policy", None),
    )
    if args.stream:
        for change in differ.diff_stream(source):
            ct_str = (
                change.change_type.value
                if hasattr(change.change_type, "value")
                else str(change.change_type)
            )
            line = f"{ct_str:16} {change.description}"
            if change.text_diff:
                line += f"  [{change.text_diff}]"
            _console.print(line)
    else:
        diff = differ.diff(source)
        _render_with_profile(diff, args, fuel=differ._config.plugin_fuel)
        result = _emit_guardrail_reports(diff, args)
        _record_diagnostics_db(diff, args)
        _exit_if_guardrail_result_fails(result)


def _cmd_patch(args: argparse.Namespace) -> None:
    if args.patch_file and args.patch_file != "-":
        patch_text = Path(args.patch_file).read_text(encoding="utf-8")
    else:
        patch_text = sys.stdin.read()

    base_text = ""
    if args.base:
        base_text = Path(args.base).read_text(encoding="utf-8")

    source = PatchSource(
        patch_text=patch_text,
        original_content=base_text if args.base else None,
    )
    differ = _differ(
        args.fuel,
        args.resolve_references,
        diagnostics=getattr(args, "diagnostics", False),
        profile_phases=getattr(args, "profile_phases", False),
        guardrails_strict=getattr(args, "guardrails_strict", False),
        config_start_path=args.base,
        guardrails_policy=getattr(args, "guardrails_policy", None),
    )
    diff = differ.diff(source)
    _render_with_profile(diff, args, fuel=differ._config.plugin_fuel)
    result = _emit_guardrail_reports(diff, args)
    _record_diagnostics_db(diff, args)
    _exit_if_guardrail_result_fails(result)


def _cmd_string(args: argparse.Namespace) -> None:
    differ = _differ(
        args.fuel,
        args.resolve_references,
        diagnostics=getattr(args, "diagnostics", False),
        profile_phases=getattr(args, "profile_phases", False),
        guardrails_strict=getattr(args, "guardrails_strict", False),
        guardrails_policy=getattr(args, "guardrails_policy", None),
    )
    diff = differ.diff_strings(
        old=args.old,
        new=args.new,
        filename=f"<string>.{args.lang}" if args.lang else "<string>",
        language_hint=args.lang,
    )
    _render_with_profile(diff, args, fuel=differ._config.plugin_fuel)
    result = _emit_guardrail_reports(diff, args)
    _record_diagnostics_db(diff, args)
    _exit_if_guardrail_result_fails(result)


def _cmd_github_pr(args: argparse.Namespace) -> None:
    from intentdiff.github_app import parse_pull_request_url

    ref = parse_pull_request_url(args.url)
    payload = {
        "kind": "github_pr",
        "owner": ref.owner,
        "repo": ref.repo,
        "number": ref.number,
        "review_command": (
            f"intentdiff-github-app review-pr --owner {shlex.quote(ref.owner)} "
            f"--repo {shlex.quote(ref.repo)} --number {ref.number}"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def _cmd_gist_diff(args: argparse.Namespace) -> None:
    from intentdiff.github_app import parse_gist_url

    ref = parse_gist_url(args.url)
    payload = {
        "kind": "gist_diff",
        "gist_id": ref.gist_id,
        "revision": ref.revision,
        "review_command": f"intentdiff gist-diff {shlex.quote(args.url)} --format html",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def _diagnostics_store(args: argparse.Namespace) -> Any:
    from intentdiff.cache.duckdb_store import DuckDBAnalyticsStore

    return DuckDBAnalyticsStore(args.db)


def _emit_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if getattr(args, "format", "terminal") == "json":
        _write_output(
            json.dumps(rows, indent=2, sort_keys=True, default=_json_default),
            getattr(args, "output", None),
        )
        return
    if not rows:
        _console.print("[yellow]No diagnostics rows found.[/yellow]")
        return
    columns = list(rows[0])
    widths = {
        column: min(
            max(len(column), *(len(str(row.get(column, ""))) for row in rows)),
            42,
        )
        for column in columns
    }
    _console.print("  ".join(column.ljust(widths[column]) for column in columns))
    _console.print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        _console.print(
            "  ".join(
                str(row.get(column, ""))[:widths[column]].ljust(widths[column])
                for column in columns
            )
        )


def _cmd_diagnostics_summary(args: argparse.Namespace) -> None:
    with _diagnostics_store(args) as store:
        payload = {
            "recent_runs": store.recent_diagnostic_runs(limit=args.limit),
            "fuel_by_language": store.fuel_by_language(limit=args.limit),
        }
    if args.format == "json":
        _write_output(
            json.dumps(payload, indent=2, sort_keys=True, default=_json_default),
            args.output,
        )
        return
    _console.print("[bold cyan]Recent diagnostics runs[/bold cyan]")
    _emit_rows(payload["recent_runs"], args)
    _console.print()
    _console.print("[bold cyan]Fuel by language[/bold cyan]")
    _emit_rows(payload["fuel_by_language"], args)


def _cmd_diagnostics_hotspots(args: argparse.Namespace) -> None:
    with _diagnostics_store(args) as store:
        rows = store.top_fuel_hotspots(limit=args.limit)
    _emit_rows(rows, args)


def _cmd_diagnostics_query(args: argparse.Namespace) -> None:
    with _diagnostics_store(args) as store:
        rows = store.query_readonly(args.sql)
    _emit_rows(rows, args)


def _cmd_guardrails_check(args: argparse.Namespace) -> None:
    from intentdiff.vcs.git_cli import NotAGitRepositoryError

    try:
        differ = _differ(
            args.fuel,
            guardrails_strict=args.strict,
            config_start_path=args.repo,
            guardrails_policy=args.guardrails_policy,
        )
        diffs = differ.diff_commit(
            repo_path=args.repo,
            old_ref=args.old,
            new_ref=args.new,
        )
        result = _guardrail_result(diffs, strict=args.strict)

        if args.annotations == "github":
            annotations = render_guardrail_annotations(result)
            if annotations:
                print(annotations)

        if args.format == "json":
            rendered = render_guardrail_json(result)
        elif args.format == "sarif":
            rendered = render_guardrail_sarif(result)
        else:
            rendered = render_guardrail_terminal(result)
        _write_output(rendered, args.output)
        _exit_if_guardrail_result_fails(result)
    except NotAGitRepositoryError:
        _err.print(f"[red]Not a git repository:[/red] {args.repo!r}")
        sys.exit(1)
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


def _cmd_plugins_list(_args: argparse.Namespace) -> None:

    from intentdiff.plugins.registry import PluginRegistry

    registry = PluginRegistry()

    # Resolve the core package provenance string once for built-in rows
    import importlib.metadata as _meta
    for _dist_name in ("intentdiff",):
        try:
            _core_dist = _meta.distribution(_dist_name)
            _core_prov = (
                f"{_core_dist.metadata.get('Name')} "
                f"{_core_dist.metadata.get('Version')}"
            )
            break
        except _meta.PackageNotFoundError:
            continue
    else:
        _core_prov = "IntentDiff"

    # ── Parser plugins ────────────────────────────────────────────────────
    parsers = sorted(registry.parsers, key=lambda p: (-p.priority, p.grammar_id))

    parser_table = Table(title="Parser plugins", show_header=True, header_style="bold")
    parser_table.add_column("Grammar ID", style="cyan")
    parser_table.add_column("Languages", style="green")
    parser_table.add_column("Mode", style="dim")
    parser_table.add_column("Priority", justify="right", style="dim")
    parser_table.add_column("Package", style="dim")

    for p in parsers:
        parser_table.add_row(
            p.grammar_id,
            ", ".join(p.language_ids),
            p.parser_mode,
            str(p.priority),
            p.provenance or _core_prov,
        )

    if not parsers:
        parser_table.add_row("[dim](none)[/dim]", "", "", "", "")

    _console.print(parser_table)
    _console.print()

    # ── Renderer plugins ──────────────────────────────────────────────────
    renderers = sorted(registry.renderers, key=lambda r: (-r.priority, r.format_name))

    renderer_table = Table(title="Renderer plugins", show_header=True, header_style="bold")
    renderer_table.add_column("Format", style="cyan")
    renderer_table.add_column("Priority", justify="right", style="dim")
    renderer_table.add_column("Package", style="dim")

    wasm_formats = {r.format_name for r in renderers}
    for r in renderers:
        renderer_table.add_row(r.format_name, str(r.priority), r.provenance or _core_prov)

    # Show built-in formats (implemented in Python, not Wasm) if not already listed
    for builtin_fmt in ("terminal", "json"):
        if builtin_fmt not in wasm_formats:
            renderer_table.add_row(builtin_fmt, "[dim]built-in[/dim]", _core_prov)

    _console.print(renderer_table)


def _cmd_plugins_add(args: argparse.Namespace) -> None:
    import os as _os

    from intentdiff.plugins.hub import (
        PluginSpec,
        RegistryEntry,
        compute_wasm_checksums,
        fetch_official_registry,
        load_plugins_file,
        pip_install,
        pip_uninstall,
        pre_install_security_check,
        save_plugins_file,
        security_check_plugin,
    )

    source: str = getattr(args, "source", "git")
    skip_verify: bool = getattr(args, "skip_verify", False)

    # Gate --skip-verify: require --yes or env var to avoid silent bypasses.
    if skip_verify:
        yes: bool = getattr(args, "yes", False)
        if not yes and not _os.environ.get("INTENTDIFF_ALLOW_SKIP_VERIFY"):
            _err.print(
                "[bold red]--skip-verify requires --yes or "
                "INTENTDIFF_ALLOW_SKIP_VERIFY=1 to prevent accidental use.[/bold red]"
            )
            sys.exit(1)
        _err.print(
            "[yellow]Warning: security verification skipped (--skip-verify).[/yellow]"
        )

    # ── 1. Consult the official registry for official plugins ─────────────────
    registry_entry: RegistryEntry | None = None
    if not args.repo and not skip_verify:
        try:
            registry = fetch_official_registry(
                strict=getattr(args, "strict_registry", False),
            )
            short = (
                args.plugin_name[len("intentdiff-"):]
                if args.plugin_name.startswith("intentdiff-")
                else args.plugin_name
            )
            registry_entry = registry.get(short)
            if registry_entry is not None:
                # #94: refuse an ABI-incompatible plugin at RESOLVE time, before any install.
                abi_error = registry_entry.abi_incompatibility()
                if abi_error is not None:
                    _err.print(f"[bold red]ABI incompatibility:[/bold red] {abi_error}")
                    _err.print("[red]Installation aborted — nothing was installed.[/red]")
                    sys.exit(1)
                # #95: warn (non-blocking) for a community-tier / unverified plugin.
                trust_warning = registry_entry.trust_warning()
                if trust_warning is not None:
                    _err.print(f"[yellow]Trust warning:[/yellow] {trust_warning}")
                _console.print(
                    f"[dim]Found '{short}' in official registry "
                    f"({registry_entry.source} {registry_entry.ref}, "
                    f"{registry_entry.effective_trust_tier()} tier)[/dim]"
                )
        except RuntimeError as exc:
            _err.print(
                f"[yellow]Warning: could not fetch official registry: {exc}\n"
                "Proceeding without registry verification.[/yellow]"
            )

    # ── 2. Build the PluginSpec (registry overrides CLI defaults if available) ─
    if registry_entry is not None and args.ref == "main" and not args.repo:
        # Use registry-pinned values when the user didn't explicitly override
        spec = registry_entry.to_spec()
    else:
        spec = PluginSpec(
            name=args.plugin_name,
            source=source,
            ref=args.ref,
            repo=args.repo,
        )

    _console.print(
        f"Installing [cyan]{spec.package_name}[/cyan] "
        f"[dim]({spec.source}: {spec.install_target})[/dim]"
    )

    # ── 3. Pre-install wasm verification (before any Python code runs) ────────
    if not skip_verify:
        pre_errors, pre_warnings = pre_install_security_check(
            spec.install_target, spec,
            allow_source=getattr(args, "allow_source_plugin", False),
        )
        for w in pre_warnings:
            _err.print(f"[yellow]Pre-install warning:[/yellow] {w}")
        if pre_errors:
            _err.print("[bold red]Pre-install security check FAILED:[/bold red]")
            for e in pre_errors:
                _err.print(f"  [red]{e}[/red]")
            _err.print("[red]Installation aborted — nothing was installed.[/red]")
            sys.exit(1)

    # ── 4. Install ────────────────────────────────────────────────────────────
    rc = pip_install(spec.install_target, spec)
    if rc != 0:
        _err.print(f"[red]pip install failed (exit code {rc})[/red]")
        sys.exit(rc)

    # ── 5. Post-install: compute checksums and run security checks ────────────
    if not skip_verify:
        # If we have no registry-supplied checksums, compute them now (TOFU)
        if not spec.wasm_checksums:
            spec.wasm_checksums = compute_wasm_checksums(spec.package_name)
            if spec.wasm_checksums:
                _console.print(
                    "[dim]Recorded wasm checksums (trust-on-first-use):[/dim]"
                )
                for fname, digest in spec.wasm_checksums.items():
                    _console.print(f"  [dim]{fname}: {digest[:16]}…[/dim]")
            else:
                _err.print(
                    "[yellow]Warning: no .wasm files found — checksums not recorded.[/yellow]"
                )

        errors, warnings = security_check_plugin(spec)
        for w in warnings:
            _err.print(f"[yellow]Security warning:[/yellow] {w}")
        if errors:
            _err.print("[bold red]Security check FAILED — quarantining plugin:[/bold red]")
            for e in errors:
                _err.print(f"  [red]{e}[/red]")
            _err.print(
                f"[red]Uninstalling '{spec.package_name}' to prevent loading "
                "a potentially tampered binary.[/red]"
            )
            pip_uninstall(spec.package_name)
            sys.exit(1)

    # ── 6. Save to manifest ───────────────────────────────────────────────────
    if args.save:
        yaml_path = Path(args.file)
        existing: list = []
        if yaml_path.exists():
            with suppress(Exception):
                existing = load_plugins_file(yaml_path)
        # Replace if already present, otherwise append
        existing = [
            s for s in existing
            if s.name != spec.name and s.name != spec.package_name
        ]
        existing.append(spec)
        save_plugins_file(yaml_path, existing)
        _console.print(f"[green]Saved to {yaml_path}[/green]")

    _console.print(f"[green]Plugin '{spec.package_name}' installed.[/green]")


def _cmd_plugins_install(args: argparse.Namespace) -> None:
    import os as _os

    from intentdiff.plugins.hub import (
        load_plugins_file,
        pip_install,
        pip_uninstall,
        pre_install_security_check,
        security_check_plugin,
    )

    skip_verify: bool = getattr(args, "skip_verify", False)

    # Gate --skip-verify.
    if skip_verify:
        yes: bool = getattr(args, "yes", False)
        if not yes and not _os.environ.get("INTENTDIFF_ALLOW_SKIP_VERIFY"):
            _err.print(
                "[bold red]--skip-verify requires --yes or "
                "INTENTDIFF_ALLOW_SKIP_VERIFY=1 to prevent accidental use.[/bold red]"
            )
            sys.exit(1)
        _err.print(
            "[yellow]Warning: security verification skipped (--skip-verify).[/yellow]"
        )

    yaml_path = Path(args.file)
    if not yaml_path.exists():
        _err.print(f"[red]Plugins file not found:[/red] {yaml_path}")
        _err.print(
            "Create one with [bold]intentdiff plugins add <name>[/bold] "
            "([bold]intentdiff plugins add <name>[/bold] remains an alias) "
            "or write it manually."
        )
        sys.exit(1)

    specs = load_plugins_file(yaml_path)
    if not specs:
        _console.print(f"[yellow]No plugins defined in[/yellow] {yaml_path}")
        return

    _console.print(f"Installing {len(specs)} plugin(s) from [dim]{yaml_path}[/dim] ...")
    failed: list[str] = []
    security_failed: list[str] = []

    for spec in specs:
        _console.print(
            f"  [cyan]{spec.package_name}[/cyan]  "
            f"[dim]({spec.source}: {spec.install_target})[/dim]"
        )

        # Pre-install wasm verification.
        if not skip_verify:
            pre_errors, pre_warnings = pre_install_security_check(
                spec.install_target, spec,
                allow_source=getattr(args, "allow_source_plugin", False),
            )
            for w in pre_warnings:
                _err.print(f"  [yellow]Pre-install warning:[/yellow] {w}")
            if pre_errors:
                _err.print(
                    f"  [bold red]Pre-install security check FAILED for "
                    f"'{spec.package_name}':[/bold red]"
                )
                for e in pre_errors:
                    _err.print(f"    [red]{e}[/red]")
                security_failed.append(spec.name)
                continue

        rc = pip_install(spec.install_target, spec, upgrade=args.upgrade)
        if rc != 0:
            _err.print(f"  [red]FAILED[/red] (exit code {rc})")
            failed.append(spec.name)
            continue

        if not skip_verify:
            errors, warnings = security_check_plugin(spec)
            for w in warnings:
                _err.print(f"  [yellow]Security warning:[/yellow] {w}")
            if errors:
                _err.print(
                    f"  [bold red]Post-install security check FAILED for "
                    f"'{spec.package_name}' — quarantining:[/bold red]"
                )
                for e in errors:
                    _err.print(f"    [red]{e}[/red]")
                _err.print(
                    f"  [red]Uninstalling '{spec.package_name}'.[/red]"
                )
                pip_uninstall(spec.package_name)
                security_failed.append(spec.name)

    if failed:
        _err.print(f"[red]{len(failed)} plugin(s) failed to install: {', '.join(failed)}[/red]")
    if security_failed:
        _err.print(
            f"[bold red]{len(security_failed)} plugin(s) failed security checks: "
            f"{', '.join(security_failed)}[/bold red]\n"
            "These plugins were refused or quarantined."
        )
    if failed or security_failed:
        sys.exit(1)
    _console.print(f"[green]All {len(specs)} plugin(s) installed and verified.[/green]")


def _cmd_security_check(args: argparse.Namespace) -> None:
    """Sync OSV advisory check: fetch if stale or --refresh, display results."""
    import time as _time_sc

    from intentdiff.plugins.loader import (
        _OSV_FETCH_INTERVAL,
        _PACKAGES_TO_AUDIT,
        _load_osv_cache,
        _osv_cache_path,
        _osv_stamp_path,
        _read_stamp,
        refresh_osv_cache,
    )

    force_refresh: bool = getattr(args, "refresh", False)
    stamp_age = _time_sc.time() - _read_stamp()
    cache = _load_osv_cache()
    needs_fetch = force_refresh or stamp_age >= _OSV_FETCH_INTERVAL or cache is None

    if needs_fetch:
        if force_refresh:
            _console.print("[dim]Forcing OSV cache refresh (--refresh)...[/dim]")
        else:
            _console.print("[dim]OSV cache is stale or absent — fetching...[/dim]")
        try:
            vulns = refresh_osv_cache()
        except Exception as exc:
            _err.print(
                f"[bold red]Failed to fetch OSV advisory database:[/bold red] {exc}\n\n"
                "The advisory cache has been marked as failed.\n"
                "Plugin loading will be blocked until you run\n"
                "  [bold]intentdiff security-check --refresh[/bold]\n"
                "successfully, or set [bold]INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1[/bold] "
                "to bypass (not recommended)."
            )
            sys.exit(1)
        _console.print("[green]OSV advisory database refreshed.[/green]")
    else:
        last = _time_sc.strftime(
            "%Y-%m-%d %H:%M:%S",
            _time_sc.localtime(_time_sc.time() - stamp_age),
        )
        _console.print(f"[dim]Using cached OSV data (last fetched: {last})[/dim]")
        vulns = cache  # type: ignore[assignment]

    _console.print(
        f"[dim]Checked {len(_PACKAGES_TO_AUDIT)} package(s) "
        "against the OSV advisory database.[/dim]"
    )
    _console.print(
        f"[dim]Cache: {_osv_cache_path()}[/dim]\n"
        f"[dim]Stamp: {_osv_stamp_path()}[/dim]"
    )

    if not vulns:
        _console.print("\n[bold green]No known vulnerabilities found.[/bold green]")
        sys.exit(0)

    table = Table(
        title=f"[bold red]OSV Advisories — {len(vulns)} finding(s)[/bold red]",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Package", style="cyan")
    table.add_column("Version")
    table.add_column("Advisory ID", style="red")
    table.add_column("Aliases", style="dim")
    table.add_column("Summary")

    for v in vulns:
        table.add_row(
            v.get("package", ""),
            v.get("version", ""),
            v.get("id", ""),
            ", ".join(v.get("aliases", [])),
            v.get("summary", ""),
        )

    _console.print(table)
    _console.print(
        "\n[bold red]Action required:[/bold red] upgrade the affected packages.\n"
        "Until resolved, plugin loading is blocked.  To bypass (not recommended),\n"
        "set [bold]INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1[/bold]."
    )
    sys.exit(1)


def _cmd_plugins_remove(args: argparse.Namespace) -> None:
    from intentdiff.plugins.hub import (
        PluginSpec,
        load_plugins_file,
        pip_uninstall,
        save_plugins_file,
    )

    name = args.plugin_name
    spec = PluginSpec(name=name)
    _console.print(f"Uninstalling [cyan]{spec.package_name}[/cyan] ...")
    rc = pip_uninstall(spec.package_name)
    if rc != 0:
        _err.print(f"[red]pip uninstall failed (exit code {rc})[/red]")
        sys.exit(rc)

    if args.save:
        yaml_path = Path(args.file)
        if yaml_path.exists():
            try:
                specs = load_plugins_file(yaml_path)
                specs = [
                    s for s in specs
                    if s.name != name and s.name != spec.package_name
                ]
                save_plugins_file(yaml_path, specs)
                _console.print(f"[green]Removed from {yaml_path}[/green]")
            except Exception as exc:
                _err.print(f"[yellow]Warning: could not update {yaml_path}: {exc}[/yellow]")

    _console.print(f"[green]Plugin '{spec.package_name}' uninstalled.[/green]")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# index command
# ---------------------------------------------------------------------------


def _cmd_index(args: argparse.Namespace) -> None:
    """Walk a git repository and pre-warm the parse/symbol-index cache."""
    import asyncio

    asyncio.run(_cmd_index_async(args))


async def _cmd_index_async(args: argparse.Namespace) -> None:
    """Entry point for the index command — creates differ/indexer, dispatches."""
    from intentdiff.core.indexer import Indexer
    from intentdiff.plugins.exceptions import PluginNotFoundError

    cache_path = Path(args.cache_path)
    fuel: int | None = getattr(args, "fuel", None)
    cfg = _load_cli_config(fuel=fuel, config_start_path=getattr(args, "repo", "."))
    cfg.cache_path = cache_path
    differ = SemanticDiffer(cfg)
    indexer = Indexer(differ)

    try:
        if getattr(args, "lsp", False):
            await _two_pass_index(args, differ, indexer)
        else:
            result = await _index_with_progress(
                indexer,
                repo_path=getattr(args, "repo", "."),
                ref=args.ref,
                force=args.force,
                lsp_clients={},
            )
            _print_index_result(result)
    except PluginNotFoundError as exc:
        _err.print(f"[red]No parser found:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        _err.print(f"[red]Indexing failed:[/red] {exc}")
        sys.exit(1)
    finally:
        if differ._cache is not None:
            differ._cache.close()


async def _two_pass_index(
    args: argparse.Namespace,
    differ: Any,
    indexer: Any,
) -> None:
    """Two-pass LSP-enriched index.

    Pass 1: plain index to detect which languages are present.
    Pass 2: re-index with LSP clients attached for type enrichment.

    Server config is read from ``lsp_servers.json`` in the repo root
    (user-defined entries override the built-in defaults).
    """
    import asyncio

    from intentdiff.lsp.client import AsyncLspClient
    from intentdiff.lsp.config import LspServerConfig
    from intentdiff.lsp.launcher import LspServerProcess
    from intentdiff.lsp.servers import KNOWN_SERVER_SPECS, load_lsp_servers_json

    repo_path: str = getattr(args, "repo", ".")
    ref: str = args.ref

    # ── Load user-defined server config ──────────────────────────────────────
    trust_repo_lsp_config: bool = getattr(args, "trust_repo_lsp_config", False)
    lsp_json = Path(repo_path) / "lsp_servers.json"
    user_entries: dict = {}
    if lsp_json.exists():
        if not trust_repo_lsp_config:
            _err.print(
                f"[yellow]Warning: {lsp_json.name} found in repository root but "
                "--trust-repo-lsp-config was not passed.  "
                "Repo-local LSP server commands are ignored to prevent command "
                "injection via untrusted repository content.  "
                "Re-run with --trust-repo-lsp-config if you own this repo.[/yellow]"
            )
        else:
            try:
                user_entries = load_lsp_servers_json(lsp_json)
                _console.print(
                    f"[dim]Loaded {len(user_entries)} LSP server(s) "
                    f"from {lsp_json.name}[/dim]"
                )
            except Exception as exc:
                _err.print(
                    f"[yellow]Warning: cannot load {lsp_json.name}: {exc}[/yellow]"
                )

    # ── Pass 1: detect languages (fast extension/heuristic scan, no Wasm parse) ──
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Column

    _console.print("[bold]Pass 1[/bold] — scanning languages…")
    with Progress(
        SpinnerColumn(),
        TextColumn(
            "[bold blue]{task.description}", table_column=Column(no_wrap=True)
        ),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        expand=False,
        console=_console,
        transient=True,
    ) as _detect_progress:
        _detect_task = _detect_progress.add_task(
            "Scanning files", total=None
        )

        def _on_detect_progress(done: int, total: int) -> None:
            _detect_progress.update(_detect_task, completed=done, total=total)

        detected, _file_count = await asyncio.to_thread(
            indexer.detect_languages, repo_path, ref,
            on_progress=_on_detect_progress,
        )

    launchable = {
        lang for lang in detected
        if lang in user_entries or lang in KNOWN_SERVER_SPECS
    }

    if not launchable:
        langs_str = ", ".join(sorted(detected)) or "none"
        _console.print(
            f"[dim]No LSP servers configured for detected languages "
            f"({langs_str}).  Running plain index.[/dim]"
        )
        result = await _index_with_progress(
            indexer,
            repo_path=repo_path,
            ref=ref,
            force=args.force,
            lsp_clients={},
            initial_total=_file_count,
        )
        _print_index_result(result)
        return

    _console.print(
        f"[bold]Pass 1 complete[/bold] — "
        f"LSP available for: [cyan]{', '.join(sorted(launchable))}[/cyan]"
    )

    # ── Start servers + connect clients ───────────────────────────────────────
    # Resolve the workspace root URI once — LSP servers need this to find
    # project files (Cargo.toml, pyproject.toml, tsconfig.json, etc.).
    root_uri = Path(repo_path).resolve().as_uri()

    launchers: dict[str, LspServerProcess] = {}
    clients: dict[str, AsyncLspClient] = {}

    # Separate languages into "already running" vs "needs auto-start".
    manual: list[str] = []
    to_start: list[str] = []
    for lang in sorted(launchable):
        entry = user_entries.get(lang)
        if entry is not None and entry.is_manual_connect:
            manual.append(lang)
        else:
            to_start.append(lang)

    # For auto-start languages, ask the user once before spawning anything.
    if to_start:
        _console.print()
        _console.print(
            "[bold]The following LSP servers will be started automatically "
            "and terminated when indexing completes:[/bold]"
        )
        for lang in to_start:
            entry = user_entries.get(lang)
            spec = entry.to_spec() if entry is not None else KNOWN_SERVER_SPECS.get(lang)
            cmd_str = " ".join(spec.command) if spec else lang
            _console.print(f"  [cyan]{lang}[/cyan]  ->  {cmd_str}")

        _console.print()
        try:
            raw = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _console.input(
                    "[bold]Start these servers?[/bold] [Y/n]: "
                ).strip().lower(),
            )
        except (EOFError, KeyboardInterrupt):
            raw = "n"

        if raw not in ("", "y", "yes"):
            _console.print(
                "[dim]Skipping auto-start. Servers that are already "
                "running will still be used.[/dim]"
            )
            to_start = []
        _console.print()

    # Connect to already-running (manual) servers first.
    for lang in manual:
        entry = user_entries[lang]
        try:
            cfg_lsp = LspServerConfig.tcp(entry.host, port=entry.port)
            client = AsyncLspClient(cfg_lsp, root_uri=root_uri)
            await client.start()
            clients[lang] = client
            _console.print(
                f"  [green]✓[/green] {lang}  (tcp, already running @ {cfg_lsp})"
            )
        except (Exception, asyncio.CancelledError) as exc:
            _err.print(f"  [yellow]⚠[/yellow]  {lang}: {exc} — skipped")
            _hint = entry.install_hint
            if _hint:
                _err.print(f"    [dim]Install: {_hint}[/dim]")

    # Spawn approved auto-start servers.
    for lang in to_start:
        entry = user_entries.get(lang)
        try:
            spec = entry.to_spec() if entry is not None else None
            launcher = LspServerProcess(lang, spec=spec)
            cfg_lsp = await launcher.start()
            client = AsyncLspClient(cfg_lsp, root_uri=root_uri)
            await client.start()
            launchers[lang] = launcher
            clients[lang] = client
            _console.print(
                f"  [green]✓[/green] {lang}  ({cfg_lsp.transport}, auto-started)"
            )
        except (Exception, asyncio.CancelledError) as exc:
            _err.print(f"  [yellow]⚠[/yellow]  {lang}: {exc} — skipped")
            _hint = (
                entry.install_hint
                if entry is not None
                else (KNOWN_SERVER_SPECS[lang].install_hint if lang in KNOWN_SERVER_SPECS else "")
            )
            if _hint:
                _err.print(f"    [dim]Install: {_hint}[/dim]")
            if lang in launchers:
                with suppress(Exception):
                    await launchers.pop(lang).stop()

    if not clients:
        _console.print(
            "[dim]No LSP servers connected; running plain index.[/dim]"
        )
        result = await _index_with_progress(
            indexer,
            repo_path=repo_path,
            ref=ref,
            force=args.force,
            lsp_clients={},
            initial_total=_file_count,
        )
        _print_index_result(result)
        return

    # ── Pass 2: enrich with LSP ───────────────────────────────────────────────
    n = len(clients)
    _console.print(
        f"[bold]Pass 2[/bold] — enriching with LSP "
        f"({n} server{'s' if n != 1 else ''})…"
    )
    try:
        result = await _index_with_progress(
            indexer,
            repo_path=repo_path,
            ref=ref,
            force=args.force,
            lsp_clients=clients,
            initial_total=_file_count,
        )
        _print_index_result(result)
    finally:
        for c in clients.values():
            with suppress(Exception):
                await c.shutdown()
        for lp in launchers.values():
            with suppress(Exception):
                await lp.stop()


async def _index_with_progress(
    indexer: Any,
    *,
    repo_path: str,
    ref: str,
    force: bool,
    lsp_clients: dict[str, Any],
    initial_total: int | None = None,
) -> Any:
    """Run the indexer with a Rich progress bar and return the result."""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Column

    from intentdiff.core.indexer import IndexProgress

    with Progress(
        SpinnerColumn(),
        TextColumn(
            "[bold blue]{task.description}", table_column=Column(no_wrap=True)
        ),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn(
            "[dim]{task.fields[current_file]}[/dim]",
            table_column=Column(no_wrap=True),
        ),
        expand=False,
        console=_console,
        transient=False,
    ) as progress:
        lsp_tag = " [dim][LSP][/dim]" if lsp_clients else ""
        task = progress.add_task(
            f"Indexing [cyan]{ref}[/cyan]{lsp_tag}",
            total=initial_total,
            current_file="collecting files…",
        )

        def _on_progress(p: IndexProgress) -> None:
            progress.update(
                task,
                total=p.total,
                completed=p.done,
                current_file=p.current_file if p.current_file else "",
            )

        if lsp_clients:
            return await indexer.index_repo_lsp(
                repo_path,
                ref=ref,
                lsp_clients=lsp_clients,
                on_progress=_on_progress,
                force=force,
            )
        else:
            return indexer.index_repo(
                repo_path, ref=ref, on_progress=_on_progress, force=force
            )


def _print_index_result(result: Any) -> None:
    """Print a human-readable summary of an :class:`IndexResult`."""
    if result.from_cache:
        _console.print("[green]Symbol index loaded from cache[/green]")
        return

    type_enriched = getattr(result, "type_enriched_count", 0)
    files_ignored = getattr(result, "files_ignored", 0)
    _console.print()
    _console.print(
        f"[green]Indexed[/green] [bold]{result.files_indexed}[/bold] file(s)  "
        f"[dim]{result.files_skipped} skipped"
        + (f"  {files_ignored} ignored" if files_ignored else "")
        + f"  {len(result.errors)} error(s)"
        + (f"  {type_enriched} type-enriched" if type_enriched else "")
        + "[/dim]"
    )

    if result.errors:
        _console.print()
        _console.print("[yellow]Parse errors:[/yellow]")
        for filename, msg in result.errors[:10]:
            _console.print(f"  [red]{filename}[/red]: {msg}")
        if len(result.errors) > 10:
            _console.print(f"  [dim]…and {len(result.errors) - 10} more[/dim]")

    if result.skipped_files:
        from collections import Counter
        from pathlib import Path as _Path

        ext_counts: Counter[str] = Counter(
            _Path(f).suffix or "(no extension)" for f in result.skipped_files
        )
        _console.print()
        _console.print(
            f"[dim]Skipped {result.files_skipped} file(s) with no registered parser "
            f"({', '.join(f'{c}×{e}' for e, c in ext_counts.most_common())})[/dim]"
        )

    ignored_files = getattr(result, "ignored_files", [])
    if ignored_files:
        _console.print()
        _console.print(
            f"[dim]Ignored {len(ignored_files)} file(s) via .diffignore[/dim]"
        )






# ---------------------------------------------------------------------------
# cache command
# ---------------------------------------------------------------------------


def _cmd_cache_stats(args: argparse.Namespace) -> None:
    """Show cache statistics including hit/miss metrics."""
    import time

    from intentdiff.cache.sqlite_store import SqliteCacheStore

    cache_path = Path(args.cache_path) / "cache.db"
    if not cache_path.exists():
        _console.print(f"[yellow]No cache found at[/yellow] {cache_path}")
        return

    store = SqliteCacheStore(cache_path)
    data = store.stats()
    met = store.metrics()
    store.close()

    table = Table(title=f"Cache: {cache_path}", show_header=True, header_style="bold")
    table.add_column("Table", style="cyan")
    table.add_column("Entries", justify="right")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Oldest entry")
    table.add_column("Newest entry")
    table.add_column("TTL (days)", justify="right")
    table.add_column("Hits", justify="right", style="green")
    table.add_column("Misses", justify="right", style="red")
    table.add_column("Hit rate", justify="right")

    total_size = 0
    total_hits = 0
    total_misses = 0

    for tbl_name, s in data.items():
        m = met.get(tbl_name, {})
        size_mb = f"{s['size_bytes'] / 1024 / 1024:.2f}"
        oldest = (
            time.strftime("%Y-%m-%d", time.localtime(s["oldest"]))
            if s["oldest"] is not None else "—"
        )
        newest = (
            time.strftime("%Y-%m-%d", time.localtime(s["newest"]))
            if s.get("newest") is not None else "—"
        )
        hits = m.get("hits", 0)
        misses = m.get("misses", 0)
        hit_rate = f"{m.get('hit_rate_pct', 0.0):.1f}%" if (hits + misses) else "—"
        table.add_row(
            tbl_name, str(s["count"]), size_mb, oldest, newest,
            str(s.get("ttl_days", "?")),
            str(hits), str(misses), hit_rate,
        )
        total_size += s["size_bytes"]
        total_hits += hits
        total_misses += misses

    total_requests = total_hits + total_misses
    combined_rate = f"{total_hits / total_requests * 100:.1f}%" if total_requests else "—"
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
        f"{total_size / 1024 / 1024:.2f}",
        "", "", "",
        f"[bold]{total_hits}[/bold]",
        f"[bold]{total_misses}[/bold]",
        f"[bold]{combined_rate}[/bold]",
    )

    _console.print(table)


def _cmd_cache_list(args: argparse.Namespace) -> None:
    """List cache entries with optional filters."""


    from intentdiff.cache.sqlite_store import SqliteCacheStore

    cache_path = Path(args.cache_path) / "cache.db"
    if not cache_path.exists():
        _console.print(f"[yellow]No cache found at[/yellow] {cache_path}")
        return

    store = SqliteCacheStore(cache_path)

    tables_to_list = (
        [_TABLE_ALIAS[args.table]]
        if args.table
        else ["parse_cache", "diff_cache", "symbol_index_cache", "hover_map_cache"]
    )

    since = _parse_date_arg(getattr(args, "since", None))
    before = _parse_date_arg(getattr(args, "before", None))

    for tbl in tables_to_list:
        rows = store.list_entries(
            tbl,
            language=getattr(args, "language", None),
            file_glob=getattr(args, "file", None),
            since=since,
            before=before,
            min_size=getattr(args, "min_size", None),
            max_size=getattr(args, "max_size", None),
            limit=args.limit,
        )
        if not rows:
            _console.print(f"[dim]No entries in {tbl}[/dim]")
            continue

        verbose: bool = getattr(args, "verbose", False)
        title = f"{tbl}  ({len(rows)} shown)"
        table = Table(title=title, show_header=True, header_style="bold")
        table.add_column("Key", style="cyan")
        if verbose:
            table.add_column("Full key", style="dim")
        if tbl == "diff_cache":
            table.add_column("Language")
            table.add_column("old → new filename")
        elif tbl == "parse_cache":
            table.add_column("Grammar ID")
        elif tbl == "symbol_index_cache":
            table.add_column("Files")
        table.add_column("Size (KB)", justify="right")
        table.add_column("Age")
        table.add_column("TTL left")

        for r in rows:
            key_prefix = r["key"][:12]
            age = _human_duration(r["age_seconds"])
            ttl_left = (
                _human_duration(max(r["expires_in_seconds"], 0))
                if r["expires_in_seconds"] > 0
                else "[red]expired[/red]"
            )
            size_kb = f"{r['size_bytes'] / 1024:.1f}"

            row_cells = [key_prefix]
            if verbose:
                row_cells.append(r["key"])
            if tbl == "diff_cache":
                row_cells.append(r.get("language", ""))
                old_f = r.get("old_filename", "")
                new_f = r.get("new_filename", "")
                row_cells.append(f"{old_f} → {new_f}" if old_f != new_f else old_f)
            elif tbl == "parse_cache":
                row_cells.append(r.get("grammar_id", ""))
            elif tbl == "symbol_index_cache":
                row_cells.append(str(r.get("file_count", "")))
            row_cells += [size_kb, age, ttl_left]
            table.add_row(*row_cells)

        _console.print(table)

    store.close()


def _cmd_cache_inspect(args: argparse.Namespace) -> None:
    """Show detailed metadata and a payload summary for a single cache entry."""
    import json
    import time

    from rich.table import Table

    from intentdiff.cache.sqlite_store import SqliteCacheStore

    cache_path = Path(args.cache_path) / "cache.db"
    if not cache_path.exists():
        _console.print(f"[yellow]No cache found at[/yellow] {cache_path}")
        return

    store = SqliteCacheStore(cache_path)
    key_prefix = args.key

    # Resolve key — try exact match first, then prefix across all (or specified) tables
    tables_to_search = (
        [_TABLE_ALIAS[args.table]]
        if getattr(args, "table", None)
        else ["parse_cache", "diff_cache", "symbol_index_cache", "hover_map_cache"]
    )

    found_table: str | None = None
    full_key: str | None = None
    for tbl in tables_to_search:
        rows = store.list_entries(tbl, limit=1000)
        matches = [r for r in rows if r["key"].startswith(key_prefix)]
        if len(matches) == 1:
            found_table = tbl
            full_key = matches[0]["key"]
            break
        if len(matches) > 1:
            _err.print(
                f"[red]Ambiguous key prefix[/red] {key_prefix!r} matches "
                f"{len(matches)} entries in {tbl}. Use a longer prefix."
            )
            store.close()
            sys.exit(1)

    if full_key is None or found_table is None:
        _err.print(f"[red]Key not found:[/red] {key_prefix!r}")
        store.close()
        sys.exit(1)

    meta = store.get_entry_metadata(full_key, found_table)
    payload_str = store.get_entry_payload(full_key, found_table)
    store.close()

    # Metadata panel
    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["created_at"]))
    ttl_left = (
        _human_duration(max(meta["expires_in_seconds"], 0))
        if meta["expires_in_seconds"] > 0
        else "[red]expired[/red]"
    )
    decompressed_size = len(payload_str.encode()) if payload_str else 0

    meta_table = Table(show_header=False, box=None, padding=(0, 1))
    meta_table.add_column("Field", style="bold cyan")
    meta_table.add_column("Value")
    meta_table.add_row("Table", found_table)
    meta_table.add_row("Key", full_key)
    meta_table.add_row("Compressed size", f"{meta['size_bytes'] / 1024:.2f} KB")
    meta_table.add_row("Decompressed size", f"{decompressed_size / 1024:.2f} KB")
    meta_table.add_row("Created at", created)
    meta_table.add_row("Age", _human_duration(meta["age_seconds"]))
    meta_table.add_row("TTL remaining", ttl_left)
    if "grammar_id" in meta:
        meta_table.add_row("Grammar ID", meta["grammar_id"])
    if "language" in meta:
        meta_table.add_row("Language", meta["language"])
    if "old_filename" in meta:
        meta_table.add_row("Old filename", meta["old_filename"])
    if "new_filename" in meta:
        meta_table.add_row("New filename", meta["new_filename"])
    if "file_count" in meta:
        meta_table.add_row("File count", str(meta["file_count"]))

    _console.print(Panel(meta_table, title="[bold]Cache Entry[/bold]", expand=False))

    if not payload_str:
        _console.print("[dim]No payload.[/dim]")
        return

    payload = json.loads(payload_str)

    # Per-table payload summary
    _console.print()
    if found_table == "diff_cache":
        changes = payload.get("changes", [])
        _console.print(
            f"[bold]SemanticDiff[/bold]  language=[cyan]{payload.get('language', '?')}[/cyan]"
            f"  changes=[yellow]{len(changes)}[/yellow]"
            f"  semantic=[green]{payload.get('has_semantic_changes', '?')}[/green]"
            f"  style_only=[dim]{payload.get('is_style_only', '?')}[/dim]"
        )
        for ch in changes[:3]:
            _console.print(
                f"  • [yellow]{ch.get('change_type', '?')}[/yellow]"
                f"  {ch.get('description', '')}"
            )
        if len(changes) > 3:
            _console.print(f"  [dim]… and {len(changes) - 3} more changes[/dim]")

    elif found_table == "parse_cache":
        _console.print(
            f"[bold]SemanticNode[/bold]  type=[cyan]{payload.get('node_type', '?')}[/cyan]"
            f"  label=[yellow]{payload.get('label', '?')}[/yellow]"
            f"  children=[green]{len(payload.get('children', []))}[/green]"
        )

    elif found_table == "symbol_index_cache":
        symbols: dict = payload.get("symbols", {})
        _console.print(f"[bold]SymbolIndex[/bold]  symbols=[yellow]{len(symbols)}[/yellow]")
        for name in list(symbols.keys())[:10]:
            _console.print(f"  • {name}")
        if len(symbols) > 10:
            _console.print(f"  [dim]… and {len(symbols) - 10} more[/dim]")

    elif found_table == "hover_map_cache":
        items = list(payload.items()) if isinstance(payload, dict) else []
        _console.print(f"[bold]HoverMap[/bold]  entries=[yellow]{len(items)}[/yellow]")
        for node_id, type_str in items[:10]:
            _console.print(f"  • [cyan]{node_id}[/cyan] → {type_str}")
        if len(items) > 10:
            _console.print(f"  [dim]… and {len(items) - 10} more[/dim]")


def _cmd_watch(args: argparse.Namespace) -> None:
    """Watch files/directories and show a semantic diff whenever a file is saved."""
    from intentdiff.watcher import FileWatcher

    fuel: int | None = getattr(args, "fuel", None)
    paths: list[str] = args.paths or ["."]
    repo_root = _resolve_repo_root(paths[0])
    ref: str = args.ref
    cfg = _load_cli_config(fuel=fuel, config_start_path=repo_root)
    differ = SemanticDiffer(cfg)

    # ── LiveServer stdin mode (no file watcher) ───────────────────────────
    if getattr(args, "live_stdin", False):
        from intentdiff.live_server import LiveServer

        live_server = LiveServer(
            differ,
            repo_path=repo_root,
            ref=ref,
            debounce=getattr(args, "live_debounce", 0.15),
            stream_analysis=getattr(args, "live_stream", False),
        )
        _err.print(
            "[bold green]LiveServer[/bold green] ready — reading requests from stdin"
        )
        try:
            live_server.start_stdin()
        except KeyboardInterrupt:
            _err.print("\n[dim]Stopped.[/dim]")
        finally:
            live_server.stop()
            if differ._cache is not None:
                differ._cache.close()
        return

    fmt: str = args.format
    output: str | None = getattr(args, "output", None)

    def _render_fn(diff: SemanticDiff) -> None:
        _render(diff, fmt, output, fuel=cfg.plugin_fuel)

    debounce: float = args.debounce

    watcher = FileWatcher(
        paths,
        differ,
        render_fn=_render_fn,
        ref=ref,
        debounce=debounce,
    )

    # ── Optional LiveServer socket ────────────────────────────────────────
    live_server: Any | None = None
    if getattr(args, "live", False):
        from intentdiff.live_server import LiveServer

        live_server = LiveServer(
            differ,
            repo_path=repo_root,
            ref=ref,
            debounce=getattr(args, "live_debounce", 0.15),
            console=_console,
            stream_analysis=getattr(args, "live_stream", False),
        )
        socket_path: str | None = getattr(args, "live_socket", None)
        addr = live_server.start_socket(socket_path)
        _console.print(f"[bold green]LiveServer[/bold green] listening on [cyan]{addr}[/cyan]")

    _console.print(
        f"[bold green]Watching[/bold green] {len(paths)} path(s) against "
        f"[cyan]{ref}[/cyan] — [dim]Ctrl+C to stop[/dim]"
    )
    try:
        watcher.start()
        watcher.wait()
    except KeyboardInterrupt:
        watcher.stop()
        if live_server is not None:
            live_server.stop()
        _console.print("\n[dim]Stopped.[/dim]")
    finally:
        if live_server is not None:
            live_server.stop()
        if differ._cache is not None:
            differ._cache.close()


def _cmd_live_server(args: argparse.Namespace) -> None:
    """Start the editor-facing LiveServer protocol endpoint."""
    from intentdiff.live_server import LiveServer, _write_line

    fuel: int | None = getattr(args, "fuel", None)
    repo_root = _resolve_repo_root(getattr(args, "repo", "."))
    cfg = _load_cli_config(fuel=fuel, config_start_path=repo_root)
    stream = bool(getattr(args, "stream", False))
    cfg.stream_analysis = stream
    differ = SemanticDiffer(cfg)

    live_server = LiveServer(
        differ,
        repo_path=repo_root,
        ref=getattr(args, "ref", "HEAD"),
        debounce=getattr(args, "debounce", 0.15),
        stream_analysis=stream,
    )

    try:
        socket_path: str | None = getattr(args, "socket", None)
        if socket_path is None:
            live_server.start_stdin()
            return

        addr = live_server.start_socket(socket_path or None)
        _write_line(
            sys.stdout,
            live_server.ready_message(transport="socket", address=addr),
        )
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        live_server.stop()
        if differ._cache is not None:
            differ._cache.close()


def _cmd_lsp_server(args: argparse.Namespace) -> None:
    """Start the IntentDiff LSP server (stdio or TCP)."""
    try:
        from intentdiff.lsp_server import create_server
    except ImportError:
        _err.print(
            "[red]Error:[/red] pygls is required for 'lsp-server'.  "
            "Install with: [bold]pip install 'intentdiff[lsp-server]'[/bold]"
        )
        sys.exit(1)

    fuel: int | None = getattr(args, "fuel", None)
    cfg = _load_cli_config(fuel=fuel)

    cache_path: str | None = getattr(args, "cache_path", None)
    if cache_path is not None:
        from intentdiff.cache.sqlite_store import SqliteCacheStore

        store = SqliteCacheStore(Path(cache_path) / "cache.db")
        cfg = cfg.model_copy(update={"cache": store})

    server = create_server(
        config=cfg,
        ref=args.ref,
        debounce=args.debounce,
    )

    tcp_port: int | None = getattr(args, "tcp", None)
    if tcp_port is not None:
        _err.print(
            f"[bold green]IntentDiff LSP[/bold green] listening on "
            f"[cyan]127.0.0.1:{tcp_port}[/cyan]"
        )
        server.start_tcp("127.0.0.1", tcp_port)
    else:
        # stdio is the default transport.
        server.start_io()


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the IntentDiff HTTP playground server."""
    try:
        import uvicorn  # noqa: F401

        from intentdiff.serve import create_app
    except ImportError:
        _err.print(
            "[red]Error:[/red] fastapi and uvicorn are required for 'serve'.  "
            "Install with: [bold]pip install 'intentdiff[serve]'[/bold]"
        )
        sys.exit(1)

    fuel: int | None = getattr(args, "fuel", None)
    cfg = _load_cli_config(fuel=fuel)

    cache_path: str | None = getattr(args, "cache_path", None)
    if cache_path is not None:
        from intentdiff.cache.sqlite_store import SqliteCacheStore

        store = SqliteCacheStore(Path(cache_path) / "cache.db")
        cfg = cfg.model_copy(update={"cache": store})

    host: str = args.host
    port: int = args.port

    # Guard against accidentally exposing the playground to the network.
    _loopback = {"127.0.0.1", "::1", "localhost"}
    if host not in _loopback and not getattr(args, "allow_remote", False):
        _err.print(
            f"[bold red]Error:[/bold red] binding to '{host}' would expose the "
            "playground on the network.  "
            "Pass --allow-remote if this is intentional."
        )
        sys.exit(1)

    cors_origins: list[str] | None = getattr(args, "cors_origins", None)
    app = create_app(config=cfg, cors_origins=cors_origins)

    # Try the requested port; fall back to a free OS-assigned port if taken.
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            _s.bind((host, port))
            _s.close()
        except OSError:
            _s.bind((host, 0))
            port = _s.getsockname()[1]
            _console.print(
                f"[yellow]Warning:[/yellow] requested port was unavailable; "
                f"binding to free port [cyan]{port}[/cyan] instead."
            )

    _console.print(
        f"[bold green]IntentDiff serve[/bold green] listening on "
        f"[cyan]http://{host}:{port}[/cyan]  "
        f"([dim]Ctrl+C to stop[/dim])"
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _cmd_cache_export(args: argparse.Namespace) -> None:
    """Export cache entries as NDJSON (one JSON object per line)."""
    import json

    from intentdiff.cache.sqlite_store import SqliteCacheStore

    cache_path = Path(args.cache_path) / "cache.db"
    if not cache_path.exists():
        _console.print(f"[yellow]No cache found at[/yellow] {cache_path}")
        return

    tables_to_export = (
        [_TABLE_ALIAS[args.table]]
        if args.table and args.table != "all"
        else ["parse_cache", "diff_cache", "symbol_index_cache", "hover_map_cache"]
    )

    store = SqliteCacheStore(cache_path)
    output_path = getattr(args, "output", None)
    fh = open(output_path, "w", encoding="utf-8") if output_path else sys.stdout  # noqa: SIM115
    try:
        count = 0
        for tbl in tables_to_export:
            for entry in store.export_entries(tbl):
                fh.write(json.dumps(entry, default=str) + "\n")
                count += 1
    finally:
        if output_path:
            fh.close()
        store.close()

    if output_path:
        _console.print(f"[green]Exported {count} entries to[/green] {output_path}")


# ---------------------------------------------------------------------------
# Date/duration helpers (used by cache list/inspect)
# ---------------------------------------------------------------------------

def _parse_date_arg(value: str | None) -> int | None:
    """Parse a YYYY-MM-DD string to a Unix epoch int, or return None."""
    if value is None:
        return None
    import time  # noqa: PLC0415
    try:
        return int(time.mktime(time.strptime(value, "%Y-%m-%d")))
    except ValueError:
        _err.print(f"[red]Invalid date format[/red]: {value!r} (expected YYYY-MM-DD)")
        sys.exit(1)


_TABLE_ALIAS: dict[str | None, str | None] = {
    "parse": "parse_cache",
    "diff": "diff_cache",
    "index": "symbol_index_cache",
    "hover": "hover_map_cache",
    None: None,
}


def _human_duration(seconds: int) -> str:
    """Return a human-readable duration string from a seconds count."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86_400}d"


def _cmd_cache_clear(args: argparse.Namespace) -> None:
    """Clear cache entries selectively."""
    from intentdiff.cache.sqlite_store import SqliteCacheStore

    cache_path = Path(args.cache_path) / "cache.db"
    if not cache_path.exists():
        _console.print(f"[yellow]No cache found at[/yellow] {cache_path}")
        return

    clear_all: bool = getattr(args, "all", False)
    clear_parse: bool = getattr(args, "parse", False) or clear_all
    clear_diff: bool = getattr(args, "diff", False) or clear_all
    clear_index: bool = getattr(args, "index", False) or clear_all

    if not any([clear_parse, clear_diff, clear_index]):
        _err.print(
            "[yellow]Specify at least one of --parse, --diff, --index, or --all[/yellow]"
        )
        sys.exit(1)

    store = SqliteCacheStore(cache_path)
    store.clear(parse=clear_parse, diff=clear_diff, index=clear_index)
    store.close()

    cleared = ", ".join(
        t
        for t, flag in [
            ("parse_cache", clear_parse),
            ("diff_cache", clear_diff),
            ("symbol_index_cache", clear_index),
        ]
        if flag
    )
    _console.print(f"[green]Cleared:[/green] {cleared}")


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--format", "-f",
        choices=["terminal", "json", "patch", "html", "llm"],
        default="terminal",
        metavar="FORMAT",
        help="Output format: terminal (default), json, patch, html, llm",
    )
    p.add_argument(
        "--output", "-o",
        metavar="FILE",
        default=None,
        help="Write output to FILE instead of stdout",
    )


def _parse_megapixels(value: str) -> int:
    try:
        megapixels = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid megapixel value {value!r}: use a positive number"
        ) from None
    if megapixels <= 0:
        raise argparse.ArgumentTypeError("max decoded megapixels must be positive")
    return max(1, int(megapixels * 1_000_000))


def _add_asset_diff_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--out",
        default=".intentdiff/assets",
        metavar="DIR",
        help="Directory for generated asset-diff artifacts.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON.",
    )
    p.add_argument(
        "--dimension-policy",
        choices=("strict", "resize", "pad"),
        default="strict",
        help="How to compare images with different dimensions.",
    )
    p.add_argument(
        "--pixel-threshold",
        type=int,
        default=16,
        metavar="N",
        help="Per-pixel channel threshold before a pixel counts as changed.",
    )
    p.add_argument(
        "--region-min-area",
        type=int,
        default=4,
        metavar="PX",
        help="Minimum connected changed-pixel region to keep.",
    )
    p.add_argument(
        "--alpha-handling",
        choices=("include", "ignore"),
        default="include",
        help="Whether alpha channel differences affect perceptual metrics.",
    )
    p.add_argument(
        "--max-decoded-megapixels",
        dest="max_decoded_pixels",
        type=_parse_megapixels,
        default=40_000_000,
        metavar="MP",
        help="Maximum decoded image size per asset, in megapixels (default: 40).",
    )



if TYPE_CHECKING:
    from intentdiff.lsp.config import LspServerConfig as LspServerConfig  # noqa: F811


def _add_fuel_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--fuel",
        type=_parse_fuel,
        default=None,
        metavar="N",
        help=(
            "Wasm fuel budget (minimum) per plugin call. "
            "By default the budget scales automatically with file size. "
            "Use -1, 'inf', or 'infinite' to remove the cap entirely (use with care)."
        ),
    )


def _add_resolve_references_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--resolve-references",
        dest="resolve_references",
        action="store_true",
        default=False,
        help=(
            "Attach a resolved SymbolDefinition to each ReferenceUsage returned "
            "by SemanticIndex.find_references() when exactly one matching "
            "definition exists in the index."
        ),
    )


def _add_diagnostics_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--diagnostics",
        action="store_true",
        default=False,
        help=(
            "Include an opt-in semantic diagnostics trace in JSON output. "
            "Terminal rendering is unchanged."
        ),
    )


def _add_diagnostics_db_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--diagnostics-db",
        metavar="FILE",
        default=None,
        help=(
            "Append normalized fuel diagnostics to a local DuckDB database. "
            "Use intentdiff diagnostics summary/query to inspect it."
        ),
    )


def _add_profile_phases_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--profile-phases",
        action="store_true",
        default=False,
        help=(
            "Emit compact phase timing JSON to stderr and attach timing metadata "
            "to diff results. Also enabled by INTENTDIFF_PROFILE_PHASES=1."
        ),
    )


def _add_guardrail_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--guardrails-strict",
        action="store_true",
        default=False,
        help=(
            "Exit non-zero when an immutable protected config guardrail "
            "violation is detected."
        ),
    )
    p.add_argument(
        "--guardrails-policy",
        metavar="PATH",
        default=None,
        help=(
            "Use a specific intentdiff.yaml policy file for protected config "
            "guardrail rules."
        ),
    )
    p.add_argument(
        "--guardrails-annotations",
        choices=("github", "none"),
        default="none",
        help="Emit guardrail-only GitHub Actions annotations.",
    )
    p.add_argument(
        "--guardrails-sarif",
        metavar="FILE",
        default=None,
        help="Write guardrail-only SARIF 2.1.0 output to FILE.",
    )
    p.add_argument(
        "--guardrails-json",
        metavar="FILE",
        default=None,
        help="Write guardrail-only JSON output to FILE.",
    )


class _GitPositionalsAction(argparse.Action):
    """Distribute optional [REPO] FILE positionals for the git sub-command.

    With one positional:  ``git FILE``        → repo=".", file=FILE
    With two positionals: ``git REPO FILE``   → repo=REPO, file=FILE
    With zero positionals:                    → repo=".", file=None (commit-wide)
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: list[str],
        option_string: str | None = None,
    ) -> None:
        if len(values) >= 2:
            namespace.repo = values[0]
            namespace.file = values[1]
        elif len(values) == 1:
            namespace.file = values[0]
        # else: keep set_defaults (repo=".", file=None)


