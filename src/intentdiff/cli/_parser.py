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
import sys
from typing import NoReturn

try:  # rich-click is a runtime dependency, but this fallback keeps source-tree tests robust.
    import rich_click as click
    _CLICK_RUNTIME_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal dev envs.
    _CLICK_RUNTIME_AVAILABLE = False
    click = None  # type: ignore[assignment]


from intentdiff import (
    __version__,
)
from intentdiff.cli._commands import (  # noqa: F401
    _add_asset_diff_args,
    _add_diagnostics_arg,
    _add_diagnostics_db_arg,
    _add_fuel_arg,
    _add_guardrail_args,
    _add_output_args,
    _add_profile_phases_arg,
    _add_resolve_references_arg,
    _cmd_assets_diff,
    _cmd_assets_git,
    _cmd_cache_clear,
    _cmd_cache_export,
    _cmd_cache_inspect,
    _cmd_cache_list,
    _cmd_cache_stats,
    _cmd_diagnostics_hotspots,
    _cmd_diagnostics_query,
    _cmd_diagnostics_summary,
    _cmd_file,
    _cmd_gist_diff,
    _cmd_git,
    _cmd_github_pr,
    _cmd_guardrails_check,
    _cmd_index,
    _cmd_live_server,
    _cmd_lsp_server,
    _cmd_patch,
    _cmd_plugins_add,
    _cmd_plugins_install,
    _cmd_plugins_list,
    _cmd_plugins_remove,
    _cmd_security_check,
    _cmd_serve,
    _cmd_string,
    _cmd_watch,
    _GitPositionalsAction,
)
from intentdiff.cli._shared import (  # noqa: F401
    _cmd_shell,
    _err,
    _parse_fuel,
    _run_parsed_command,
    _version_text,
    _warn_if_unlimited_fuel,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intentdiff",
        description=(
            "Semantic code review: detect intent, moves, refactorings, and "
            "style changes."
        ),
    )
    parser.add_argument("--version", action="version", version=_version_text())
    parser.add_argument(
        "--no-banner",
        action="store_true",
        default=False,
        help="Suppress the interactive IntentDiff banner.",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    shell_p = sub.add_parser(
        "shell",
        help="Start the interactive IntentDiff command shell.",
        description=(
            "Start a lightweight IntentDiff shell. Commands entered at the "
            "prompt use the same parser and behavior as the normal CLI."
        ),
    )
    shell_p.add_argument(
        "--no-banner",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Start the shell without the IntentDiff banner.",
    )
    shell_p.set_defaults(func=_cmd_shell)

    # ── git ──────────────────────────────────────────────────────────────────
    git_p = sub.add_parser("git", help="Diff a file between two git commits.")
    git_p.set_defaults(repo=".", file=None)
    git_p.add_argument(
        "_git_pos", nargs="*", action=_GitPositionalsAction,
        metavar="[REPO] [FILE]",
        help="Optional repository root followed by file path. "
             "Omit FILE to diff all changed files; omit REPO to use current directory.",
    )
    git_p.add_argument(
        "--old", default=None, metavar="REF",
        help=(
            "Old git ref to compare from. "
            "Defaults to HEAD when --new is the working tree (default mode) or a scope flag, "
            "and to HEAD~1 when --new is an explicit commit ref."
        ),
    )
    git_p.add_argument("--new", default="", metavar="REF",
                       help=(
                           "New git ref (default: working tree; HEAD plus staged). "
                           "Pass a commit ref to do commit-to-commit diff."
                       ))
    git_p.add_argument(
        "--staged", action="store_true", default=False,
        help="Diff HEAD against the git index (staged files only).",
    )
    git_p.add_argument(
        "--unpushed", action="store_true", default=False,
        help="Diff the remote tracking branch against HEAD (commits not yet pushed).",
    )
    _add_output_args(git_p)
    _add_fuel_arg(git_p)
    _add_resolve_references_arg(git_p)
    _add_diagnostics_arg(git_p)
    _add_diagnostics_db_arg(git_p)
    _add_profile_phases_arg(git_p)
    _add_guardrail_args(git_p)
    git_p.add_argument(
        "--parallel",
        nargs="?",
        const=True,
        type=int,
        default=None,
        metavar="N",
        help=(
            "Parallelise commit-wide diff across threads. "
            "Omit N to use cpu_count(); pass N for an explicit worker count."
        ),
    )
    git_p.set_defaults(func=_cmd_git)

    # ── assets ─────────────────────────────────────────────────────────────
    assets_p = sub.add_parser(
        "assets",
        help="Perceptual diffs for non-text assets.",
    )
    assets_sub = assets_p.add_subparsers(dest="assets_command", metavar="ACTION")
    assets_sub.required = True

    assets_diff_p = assets_sub.add_parser(
        "diff",
        help="Compare two image assets and generate perceptual artifacts.",
    )
    assets_diff_p.add_argument("--before", required=True, metavar="PATH")
    assets_diff_p.add_argument("--after", required=True, metavar="PATH")
    assets_diff_p.add_argument(
        "--file-path",
        default=None,
        metavar="PATH",
        help="Logical repository path to include in JSON output.",
    )
    _add_asset_diff_args(assets_diff_p)
    assets_diff_p.set_defaults(func=_cmd_assets_diff)

    assets_git_p = assets_sub.add_parser(
        "git",
        help="Discover changed image assets in git and generate perceptual diffs.",
    )
    assets_git_p.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="Repository path.",
    )
    assets_git_p.add_argument("--base", default=None, metavar="REF")
    assets_git_p.add_argument(
        "--head",
        default="",
        metavar="REF",
        help="Head ref, or omit for working-tree changes.",
    )
    assets_git_p.add_argument(
        "--staged",
        action="store_true",
        default=False,
        help="Compare HEAD/base against the git index.",
    )
    assets_git_p.add_argument(
        "--unpushed",
        action="store_true",
        default=False,
        help="Compare the upstream tracking branch against HEAD.",
    )
    _add_asset_diff_args(assets_git_p)
    assets_git_p.set_defaults(func=_cmd_assets_git)

    # ── file ─────────────────────────────────────────────────────────────────
    file_p = sub.add_parser("file", help="Diff two local files.")
    file_p.add_argument("old_file", metavar="OLD", help="Path to the old file.")
    file_p.add_argument("new_file", metavar="NEW", help="Path to the new file.")
    _add_output_args(file_p)
    _add_fuel_arg(file_p)
    _add_resolve_references_arg(file_p)
    _add_diagnostics_arg(file_p)
    _add_diagnostics_db_arg(file_p)
    _add_profile_phases_arg(file_p)
    _add_guardrail_args(file_p)
    file_p.add_argument(
        "--stream",
        action="store_true",
        help="Yield changes as they are computed rather than waiting for the full diff.",
    )
    file_p.set_defaults(func=_cmd_file)

    # ── patch ─────────────────────────────────────────────────────────────────
    patch_p = sub.add_parser("patch", help="Diff from a unified diff patch (stdin or file).")
    patch_p.add_argument("patch_file", nargs="?", default="-", metavar="PATCH",
                         help="Path to patch file, or '-' / omit to read from stdin.")
    patch_p.add_argument("--base", metavar="BASE", default=None,
                         help="Path to the original file (needed to reconstruct context).")
    _add_output_args(patch_p)
    _add_fuel_arg(patch_p)
    _add_resolve_references_arg(patch_p)
    _add_diagnostics_arg(patch_p)
    _add_diagnostics_db_arg(patch_p)
    _add_profile_phases_arg(patch_p)
    _add_guardrail_args(patch_p)
    patch_p.set_defaults(func=_cmd_patch)

    # ── string ────────────────────────────────────────────────────────────────
    str_p = sub.add_parser("string", help="Diff two in-memory strings (useful for scripting).")
    str_p.add_argument("old", metavar="OLD", help="Old source code string.")
    str_p.add_argument("new", metavar="NEW", help="New source code string.")
    str_p.add_argument("--lang", metavar="LANG", default=None,
                       help="Language hint (e.g. python, sql).")
    _add_output_args(str_p)
    _add_fuel_arg(str_p)
    _add_resolve_references_arg(str_p)
    _add_diagnostics_arg(str_p)
    _add_diagnostics_db_arg(str_p)
    _add_profile_phases_arg(str_p)
    _add_guardrail_args(str_p)
    str_p.set_defaults(func=_cmd_string)

    github_pr_p = sub.add_parser(
        "github-pr",
        help="Parse a GitHub pull request URL into an IntentDiff review target.",
    )
    github_pr_p.add_argument("url", metavar="URL", help="https://github.com/OWNER/REPO/pull/NUMBER")
    github_pr_p.set_defaults(func=_cmd_github_pr)

    gist_diff_p = sub.add_parser(
        "gist-diff",
        help="Parse a GitHub Gist URL into an IntentDiff diff target.",
    )
    gist_diff_p.add_argument("url", metavar="URL", help="https://gist.github.com/OWNER/GIST_ID")
    gist_diff_p.add_argument("--format", default="html", choices=("json", "html"))
    gist_diff_p.set_defaults(func=_cmd_gist_diff)

    guardrails_p = sub.add_parser(
        "guardrails",
        help="Check protected config guardrail policy.",
    )
    guardrails_sub = guardrails_p.add_subparsers(
        dest="guardrails_cmd",
        metavar="ACTION",
    )
    guardrails_sub.required = True
    check_p = guardrails_sub.add_parser(
        "check",
        help="Run guardrail policy checks for a git diff.",
    )
    check_p.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="Repository path to check.",
    )
    check_p.add_argument("--old", default="HEAD~1", metavar="REF")
    check_p.add_argument("--new", default="HEAD", metavar="REF")
    check_p.add_argument(
        "--policy",
        dest="guardrails_policy",
        default=None,
        metavar="PATH",
        help="Use a specific intentdiff.yaml policy file.",
    )
    check_p.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit 2 when immutable guardrail violations are found.",
    )
    _add_fuel_arg(check_p)
    check_p.add_argument(
        "--format",
        choices=("terminal", "json", "sarif"),
        default="terminal",
        help="Guardrail check report format.",
    )
    check_p.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write guardrail check report to FILE.",
    )
    check_p.add_argument(
        "--annotations",
        choices=("github", "none"),
        default="none",
        help="Emit GitHub Actions annotations in addition to the report.",
    )
    check_p.set_defaults(func=_cmd_guardrails_check)

    # ── plugins ───────────────────────────────────────────────────────────────
    plugins_p = sub.add_parser(
        "plugins",
        help="Manage parser and renderer plugins.",
    )
    # Default to 'list' when called without a subcommand
    plugins_p.set_defaults(func=_cmd_plugins_list, file="intentdiff_plugins.yaml")
    plugins_sub = plugins_p.add_subparsers(dest="plugins_cmd", metavar="ACTION")

    # plugins list
    pl_list = plugins_sub.add_parser("list", help="List installed plugins (default).")
    pl_list.set_defaults(func=_cmd_plugins_list)

    # plugins add
    pl_add = plugins_sub.add_parser(
        "add",
        help="Download and install a plugin from the official org or a custom repo.",
    )
    pl_add.add_argument(
        "plugin_name", metavar="NAME",
        help="Short name (e.g. dbt) or full package name (intentdiff-dbt).",
    )
    pl_add.add_argument(
        "--ref", default="main", metavar="REF",
        help="Git tag/branch/commit or PyPI version to install (default: main).",
    )
    pl_add.add_argument(
        "--source", default="git", choices=["git", "pypi"],
        help="Installation source: 'git' (default) or 'pypi'.",
    )
    pl_add.add_argument(
        "--repo", default="", metavar="URL",
        help="Custom git repo URL. Omit to use the official intentdiff registry org.",
    )
    pl_add.add_argument(
        "--file", default="intentdiff_plugins.yaml", metavar="FILE",
        help="Plugins manifest to update (default: intentdiff_plugins.yaml).",
    )
    pl_add.add_argument(
        "--no-save", dest="save", action="store_false",
        help="Install without updating intentdiff_plugins.yaml.",
    )
    pl_add.add_argument(
        "--skip-verify", dest="skip_verify", action="store_true",
        help="Skip Wasm checksum and capability checks (not recommended).",
    )
    pl_add.add_argument(
        "--allow-source-plugin", dest="allow_source_plugin", action="store_true",
        help="Allow installing from VCS/local/direct-URL sources (disables binary-only check).",
    )
    pl_add.add_argument(
        "--strict-registry", dest="strict_registry", action="store_true",
        help=(
            "Require the official registry ref to be a full 40-character commit SHA. "
            "Rejects mutable refs (branch names, tags) for reproducible, tamper-evident installs. "
            "Recommended for CI and production environments."
        ),
    )
    pl_add.set_defaults(
        save=True,
        skip_verify=False,
        allow_source_plugin=False,
        strict_registry=False,
        func=_cmd_plugins_add,
    )

    # plugins install
    pl_install = plugins_sub.add_parser(
        "install",
        help="Install all plugins declared in intentdiff_plugins.yaml.",
    )
    pl_install.add_argument(
        "--file", default="intentdiff_plugins.yaml", metavar="FILE",
        help="Plugins manifest to read (default: intentdiff_plugins.yaml).",
    )
    pl_install.add_argument(
        "--upgrade", action="store_true",
        help="Pass --upgrade to pip so already-installed plugins are updated.",
    )
    pl_install.add_argument(
        "--skip-verify", dest="skip_verify", action="store_true",
        help="Skip Wasm checksum and capability checks (not recommended).",
    )
    pl_install.add_argument(
        "--allow-source-plugin", dest="allow_source_plugin", action="store_true",
        help="Allow installing from VCS/local/direct-URL sources (disables binary-only check).",
    )
    pl_install.add_argument(
        "--strict-registry", dest="strict_registry", action="store_true",
        help=(
            "Require the official registry ref to be a full 40-character commit SHA. "
            "Rejects mutable refs for reproducible, tamper-evident installs. "
            "Recommended for CI and production environments."
        ),
    )
    pl_install.set_defaults(
        skip_verify=False,
        allow_source_plugin=False,
        strict_registry=False,
        func=_cmd_plugins_install,
    )

    # plugins remove
    pl_remove = plugins_sub.add_parser(
        "remove",
        help="Uninstall a plugin and remove it from intentdiff_plugins.yaml.",
    )
    pl_remove.add_argument(
        "plugin_name", metavar="NAME",
        help="Short name or full package name to uninstall.",
    )
    pl_remove.add_argument(
        "--file", default="intentdiff_plugins.yaml", metavar="FILE",
        help="Plugins manifest to update (default: intentdiff_plugins.yaml).",
    )
    pl_remove.add_argument(
        "--no-save", dest="save", action="store_false",
        help="Uninstall without updating intentdiff_plugins.yaml.",
    )
    pl_remove.set_defaults(save=True, func=_cmd_plugins_remove)

    # ── security-check ────────────────────────────────────────────────────────
    sc_p = sub.add_parser(
        "security-check",
        help="Check installed packages against the OSV advisory database.",
        description=(
            "Queries the OSV (Open Source Vulnerabilities) API for known "
            "vulnerabilities in installed packages.  Results are cached for "
            "24 hours.\n\n"
            "By default this command reads from cache if it is fresh.  Use "
            "--refresh to force an immediate re-check — this is also the "
            "documented way to unblock plugin loading after a failed advisory "
            "check."
        ),
    )
    sc_p.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help=(
            "Bypass the 24-hour cache and force a fresh OSV fetch.  "
            "Use this to unblock plugin loading after a network failure "
            "caused the advisory check to fail."
        ),
    )
    sc_p.set_defaults(func=_cmd_security_check)

    # ── index ─────────────────────────────────────────────────────────────
    index_p = sub.add_parser(
        "index",
        help="Pre-index a git repository to warm the parse and symbol-index cache.",
        description=(
            "Walk every text file in the repository at the given ref, "
            "parse each one, and store the results in the local cache "
            "directory.  Subsequent diff calls will be faster because the "
            "Wasm parse step is skipped for already-cached files.\n\n"
            "The symbol index is also persisted so that a second call for "
            "the same commit returns immediately from cache (use --force to "
            "rebuild)."
        ),
    )
    index_p.add_argument(
        "repo",
        nargs="?",
        default=".",
        metavar="REPO",
        help="Path to the git repository to index (default: current directory).",
    )
    index_p.add_argument(
        "--ref",
        default="HEAD",
        metavar="REF",
        help="Git ref to index (default: HEAD).",
    )
    index_p.add_argument(
        "--cache-path",
        default=".intentdiff-cache",
        metavar="PATH",
        dest="cache_path",
        help="Cache directory (default: .intentdiff-cache inside the repo).",
    )
    index_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-index even if a cached symbol index already exists for this commit.",
    )
    index_p.add_argument(
        "--fuel",
        type=_parse_fuel,
        default=None,
        metavar="N",
        help="Wasm fuel budget per parser call. Use -1, 'inf', or 'infinite' to remove the cap.",
    )
    index_p.add_argument(
        "--lsp",
        action="store_true",
        default=False,
        help=(
            "Enable LSP type enrichment via a two-pass index.  "
            "Pass 1 detects languages; Pass 2 starts the required servers "
            "(from lsp_servers.json in the repo root, or built-in defaults) "
            "and re-indexes with type information attached."
        ),
    )
    index_p.add_argument(
        "--trust-repo-lsp-config",
        dest="trust_repo_lsp_config",
        action="store_true",
        default=False,
        help=(
            "Allow custom LSP server commands defined in lsp_servers.json "
            "inside the repository root.  Without this flag, any "
            "repo-local lsp_servers.json is ignored and only built-in "
            "known-good server specs are used.  Use this flag only in "
            "repositories you control."
        ),
    )
    index_p.set_defaults(func=_cmd_index)

    # ── cache ─────────────────────────────────────────────────────────────
    cache_p = sub.add_parser(
        "cache",
        help="Inspect or manage the local cache database.",
    )
    cache_p.set_defaults(cache_path=".intentdiff-cache")
    cache_sub = cache_p.add_subparsers(dest="cache_command", metavar="SUBCOMMAND")
    cache_sub.required = True

    _cache_path_arg = dict(
        dest="cache_path",
        default=".intentdiff-cache",
        metavar="PATH",
        help="Cache directory (default: .intentdiff-cache).",
    )

    # cache stats
    cs = cache_sub.add_parser("stats", help="Show cache size, row counts, and hit/miss metrics.")
    cs.add_argument("--cache-path", **_cache_path_arg)
    cs.set_defaults(func=_cmd_cache_stats)

    # cache clear
    cc = cache_sub.add_parser("clear", help="Delete cache entries.")
    cc.add_argument("--cache-path", **_cache_path_arg)
    cc.add_argument("--parse", action="store_true", help="Clear the parse-tree cache.")
    cc.add_argument("--diff", action="store_true", help="Clear the diff-result cache.")
    cc.add_argument("--index", action="store_true", help="Clear the symbol-index cache.")
    cc.add_argument(
        "--all",
        action="store_true",
        help="Clear all cache tables (equivalent to --parse --diff --index).",
    )
    cc.set_defaults(func=_cmd_cache_clear)

    # cache list
    cl = cache_sub.add_parser("list", help="List cache entries with optional filters.")
    cl.add_argument("--cache-path", **_cache_path_arg)
    cl.add_argument(
        "--table", choices=["parse", "diff", "index", "hover"],
        metavar="TABLE", default=None,
        help="Restrict to one table: parse, diff, index, hover.",
    )
    cl.add_argument("--language", metavar="LANG", default=None,
                    help="Filter diff_cache by language (e.g. python).")
    cl.add_argument("--file", metavar="GLOB", default=None,
                    help="Filter diff_cache filenames by glob pattern (e.g. '*.py').")
    cl.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                    help="Only entries created on or after this date.")
    cl.add_argument("--before", metavar="YYYY-MM-DD", default=None,
                    help="Only entries created on or before this date.")
    cl.add_argument("--min-size", type=int, metavar="BYTES", default=None,
                    dest="min_size", help="Minimum compressed size in bytes.")
    cl.add_argument("--max-size", type=int, metavar="BYTES", default=None,
                    dest="max_size", help="Maximum compressed size in bytes.")
    cl.add_argument("--limit", type=int, default=50, metavar="N",
                    help="Maximum rows per table (default: 50).")
    cl.add_argument("--verbose", action="store_true",
                    help="Show full key and all metadata columns.")
    cl.set_defaults(func=_cmd_cache_list)

    # cache inspect
    ci = cache_sub.add_parser(
        "inspect",
        help="Show metadata and payload summary for one cache entry.",
    )
    ci.add_argument("key", metavar="KEY",
                    help="Full 64-char key or unambiguous prefix.")
    ci.add_argument("--cache-path", **_cache_path_arg)
    ci.add_argument(
        "--table", choices=["parse", "diff", "index", "hover"],
        metavar="TABLE", default=None,
        help="Restrict key lookup to one table.",
    )
    ci.set_defaults(func=_cmd_cache_inspect)

    # cache export
    ce = cache_sub.add_parser("export", help="Export cache entries as NDJSON.")
    ce.add_argument("--cache-path", **_cache_path_arg)
    ce.add_argument(
        "--table",
        choices=["parse", "diff", "index", "hover", "all"],
        default="all", metavar="TABLE",
        help="Table to export: parse, diff, index, hover, or all (default).",
    )
    ce.add_argument("--output", metavar="FILE", default=None,
                    help="Output file path. Defaults to stdout.")
    ce.set_defaults(func=_cmd_cache_export)

    # ── diagnostics ───────────────────────────────────────────────────────
    diagnostics_p = sub.add_parser(
        "diagnostics",
        help="Query local DuckDB fuel diagnostics.",
        description=(
            "Inspect normalized parser fuel telemetry recorded with "
            "--diagnostics-db. The VS Code UI keeps recent workspace history; "
            "this command queries durable local DuckDB history."
        ),
    )
    diagnostics_sub = diagnostics_p.add_subparsers(dest="diagnostics_command", metavar="SUBCOMMAND")
    diagnostics_sub.required = True
    _diagnostics_db_arg = dict(
        dest="db",
        default=".intentdiff/diagnostics.duckdb",
        metavar="FILE",
        help="Diagnostics DuckDB path (default: .intentdiff/diagnostics.duckdb).",
    )

    diag_summary = diagnostics_sub.add_parser(
        "summary",
        help="Show recent runs and aggregate fuel by language.",
    )
    diag_summary.add_argument("--db", **_diagnostics_db_arg)
    diag_summary.add_argument("--limit", type=int, default=10, metavar="N")
    diag_summary.add_argument("--format", choices=("terminal", "json"), default="terminal")
    diag_summary.add_argument("--output", metavar="FILE", default=None)
    diag_summary.set_defaults(func=_cmd_diagnostics_summary)

    diag_hotspots = diagnostics_sub.add_parser(
        "hotspots",
        help="Show highest normalized parser fuel hotspots.",
    )
    diag_hotspots.add_argument("--db", **_diagnostics_db_arg)
    diag_hotspots.add_argument("--limit", type=int, default=20, metavar="N")
    diag_hotspots.add_argument("--format", choices=("terminal", "json"), default="terminal")
    diag_hotspots.add_argument("--output", metavar="FILE", default=None)
    diag_hotspots.set_defaults(func=_cmd_diagnostics_hotspots)

    diag_query = diagnostics_sub.add_parser(
        "query",
        help="Run a read-only SQL query against diagnostics tables.",
    )
    diag_query.add_argument("sql", metavar="SQL")
    diag_query.add_argument("--db", **_diagnostics_db_arg)
    diag_query.add_argument("--format", choices=("terminal", "json"), default="json")
    diag_query.add_argument("--output", metavar="FILE", default=None)
    diag_query.set_defaults(func=_cmd_diagnostics_query)

    # ── watch ─────────────────────────────────────────────────────────────────
    watch_p = sub.add_parser(
        "watch",
        help="Watch files/directories and show a semantic diff on each save.",
        description=(
            "Re-diffs each changed file against its last committed state "
            "(WorkingTreeSource) whenever the file is modified on disk. "
            "Uses OS-native file-system events (watchdog). "
            "Press Ctrl+C to stop."
        ),
    )
    watch_p.add_argument(
        "paths",
        nargs="*",
        default=["."],
        metavar="PATH",
        help="Files or directories to watch (default: current directory).",
    )
    watch_p.add_argument(
        "--ref",
        default="HEAD",
        metavar="REF",
        help="Git ref to compare the working tree against (default: HEAD).",
    )
    watch_p.add_argument(
        "--debounce",
        type=float,
        default=0.3,
        metavar="SEC",
        help="Debounce delay in seconds — coalesces rapid editor saves (default: 0.3).",
    )
    _add_output_args(watch_p)
    _add_fuel_arg(watch_p)
    # ── LiveServer options ────────────────────────────────────────────────
    live_group = watch_p.add_argument_group(
        "live server",
        "Options for the persistent keystroke-level LiveServer (optional).",
    )
    live_group.add_argument(
        "--live",
        action="store_true",
        default=False,
        help=(
            "Also start a LiveServer socket alongside the file watcher.  "
            "Prints the socket address to stdout."
        ),
    )
    live_group.add_argument(
        "--live-stdin",
        action="store_true",
        default=False,
        help=(
            "Run in LiveServer stdin mode: read JSON requests from stdin and "
            "write responses to stdout.  No file watcher is started.  "
            "Useful when a socket is unavailable."
        ),
    )
    live_group.add_argument(
        "--live-socket",
        metavar="PATH",
        default=None,
        help="Custom Unix-socket path for the LiveServer (default: auto-generated).",
    )
    live_group.add_argument(
        "--live-debounce",
        type=float,
        default=0.15,
        metavar="SEC",
        help="LiveServer debounce delay in seconds (default: 0.15).",
    )
    live_group.add_argument(
        "--live-stream",
        action="store_true",
        default=False,
        help=(
            "Send progressive ChangeStreamEvent responses instead of a single "
            "SemanticDiff per request."
        ),
    )
    watch_p.set_defaults(func=_cmd_watch)

    # -- live-server --------------------------------------------------------
    live_p = sub.add_parser(
        "live-server",
        help="Start the editor-ready LiveServer JSON protocol.",
        description=(
            "Run IntentDiff as a persistent JSON LiveServer endpoint. "
            "Stdio is the default transport and writes JSON protocol messages "
            "only to stdout."
        ),
    )
    live_p.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="Repository root for repository-relative live buffer paths.",
    )
    live_transport = live_p.add_mutually_exclusive_group()
    live_transport.add_argument(
        "--stdio",
        action="store_true",
        default=False,
        help="Use stdin/stdout transport (default).",
    )
    live_transport.add_argument(
        "--socket",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Listen on a Unix socket or Windows named pipe; omit PATH to auto-generate.",
    )
    live_p.add_argument(
        "--ref",
        default="HEAD",
        metavar="REF",
        help="Default git ref to compare live buffers against (default: HEAD).",
    )
    live_p.add_argument(
        "--debounce",
        type=float,
        default=0.15,
        metavar="SEC",
        help="Keystroke debounce delay in seconds (default: 0.15).",
    )
    live_p.add_argument(
        "--stream",
        action="store_true",
        default=False,
        help="Stream progressive semantic events by default for diff requests.",
    )
    _add_fuel_arg(live_p)
    live_p.set_defaults(func=_cmd_live_server)

    # ── lsp-server ────────────────────────────────────────────────────────────
    lsp_p = sub.add_parser(
        "lsp-server",
        help="Start a pygls Language Server (stdio or TCP) for editor integration.",
        description=(
            "Run IntentDiff as an LSP server backed by pygls.  "
            "Editors connect via stdio (default) or TCP, and receive semantic "
            "diagnostics and code-lens decorations as you type.  "
            "Requires: pip install 'intentdiff[lsp-server]'"
        ),
    )
    _transport_group = lsp_p.add_mutually_exclusive_group()
    _transport_group.add_argument(
        "--stdio",
        action="store_true",
        default=False,
        help="Use stdin/stdout transport (default when neither --stdio nor --tcp is given).",
    )
    _transport_group.add_argument(
        "--tcp",
        type=int,
        metavar="PORT",
        default=None,
        help="Listen on TCP localhost:PORT instead of stdio.",
    )
    lsp_p.add_argument(
        "--ref",
        default="HEAD",
        metavar="REF",
        help="Git ref to compare live buffers against (default: HEAD).",
    )
    lsp_p.add_argument(
        "--debounce",
        type=float,
        default=0.15,
        metavar="SEC",
        help="Keystroke debounce delay in seconds (default: 0.15).",
    )
    _add_fuel_arg(lsp_p)
    lsp_p.add_argument(
        "--cache-path",
        metavar="DIR",
        default=None,
        help="Directory for the diff cache (optional).",
    )
    lsp_p.set_defaults(func=_cmd_lsp_server)

    # ── serve ─────────────────────────────────────────────────────────────
    serve_p = sub.add_parser(
        "serve",
        help="Start the HTTP diff playground server.",
        description=(
            "Start a local HTTP server exposing POST /diff "
            "(JSON body: {old, new, language}) → SemanticDiff JSON, "
            "plus a Monaco-editor web UI at http://HOST:PORT/."
        ),
    )
    serve_p.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="HOST",
        help="Host to bind (default: 127.0.0.1).",
    )
    serve_p.add_argument(
        "--port",
        type=int,
        default=7234,
        metavar="PORT",
        help="Port to bind (default: 7234).",
    )
    _add_fuel_arg(serve_p)
    serve_p.add_argument(
        "--cache-path",
        metavar="DIR",
        default=None,
        help="Directory for the diff cache (optional).",
    )
    serve_p.add_argument(
        "--cors-origins",
        dest="cors_origins",
        metavar="ORIGIN",
        nargs="+",
        default=None,
        help=(
            "Allowed CORS origins (e.g. http://localhost:3000).  "
            "When omitted, no CORS headers are emitted.  "
            "Use with care — do not pass '*' in production."
        ),
    )
    serve_p.add_argument(
        "--allow-remote",
        dest="allow_remote",
        action="store_true",
        default=False,
        help=(
            "Allow binding to a non-loopback address.  "
            "Required when --host is not 127.0.0.1 or ::1.  "
            "Use only when you understand the exposure."
        ),
    )
    serve_p.set_defaults(func=_cmd_serve)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_CLICK_CONTEXT_SETTINGS = {
    "help_option_names": [],
    "ignore_unknown_options": True,
    "allow_extra_args": True,
}


if _CLICK_RUNTIME_AVAILABLE:

    def _click_command(command: str, *, help_text: str) -> click.Command:
        @click.command(
            name=command,
            help=help_text,
            context_settings=_CLICK_CONTEXT_SETTINGS,
        )
        @click.pass_context
        def _delegate(ctx: click.Context) -> None:
            parser = _build_parser()
            words = [command, *ctx.args]
            args = parser.parse_args(words)
            parent_no_banner = bool((ctx.obj or {}).get("no_banner"))
            if parent_no_banner:
                args.no_banner = True
            args._argv = words
            _run_parsed_command(args)

        return _delegate


    @click.group(
        context_settings={"help_option_names": ["-h", "--help"]},
        invoke_without_command=False,
        help=(
            "Semantic code review: detect intent, moves, refactorings, "
            "style changes, and guardrail risks."
        ),
    )
    @click.version_option(__version__, "--version", message="IntentDiff %(version)s")
    @click.option(
        "--no-banner",
        is_flag=True,
        default=False,
        help="Suppress the interactive IntentDiff banner.",
    )
    @click.pass_context
    def _click_cli(ctx: click.Context, no_banner: bool) -> None:
        ctx.ensure_object(dict)
        ctx.obj["no_banner"] = no_banner


    for _name, _help in [
        ("shell", "Start the interactive IntentDiff command shell."),
        ("git", "Diff files or commits in a git repository."),
        ("assets", "Perceptual diffs for non-text assets."),
        ("file", "Diff two local files."),
        ("patch", "Diff from a unified diff patch."),
        ("string", "Diff two in-memory strings."),
        ("github-pr", "Parse a GitHub pull request URL into a review target."),
        ("gist-diff", "Parse a GitHub Gist URL into a diff target."),
        ("guardrails", "Check protected config guardrail policy."),
        ("plugins", "Manage parser and renderer plugins."),
        ("security-check", "Check installed packages against OSV advisories."),
        ("index", "Pre-index a git repository to warm caches."),
        ("cache", "Inspect or manage the local cache database."),
        ("diagnostics", "Query local DuckDB fuel diagnostics."),
        ("watch", "Watch files/directories and show semantic diffs on save."),
        ("live-server", "Start the editor-ready LiveServer JSON protocol."),
        ("lsp-server", "Start the pygls Language Server for editor integration."),
        ("serve", "Start the HTTP diff playground server."),
    ]:
        _click_cli.add_command(_click_command(_name, help_text=_help))

    def _legacy_click_main(argv: list[str] | None = None) -> NoReturn:
        normalized = _normalize_argv(argv)
        try:
            _click_cli.main(
                args=normalized,
                prog_name="intentdiff",
                standalone_mode=False,
                obj={},
            )
            sys.exit(0)
        except KeyboardInterrupt:
            sys.exit(130)
        except click.ClickException as exc:
            exc.show()
            sys.exit(exc.exit_code)
        except click.exceptions.Exit as exc:
            sys.exit(exc.exit_code)
        except Exception as exc:  # noqa: BLE001
            _err.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)
else:

    def _legacy_click_main(argv: list[str] | None = None) -> NoReturn:
        parser = _build_parser()
        normalized = _normalize_argv(argv)
        try:
            args = parser.parse_args(normalized)
            args._argv = normalized
            _run_parsed_command(args, show_banner=True)
            sys.exit(0)
        except KeyboardInterrupt:
            sys.exit(130)
        except argparse.ArgumentError as exc:
            _err.print(f"[red]Error:[/red] {exc}")
            sys.exit(2)
        except Exception as exc:  # noqa: BLE001
            _err.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    raw = sys.argv[1:] if argv is None else list(argv)
    if len(raw) >= 2 and raw[0] == "git" and raw[1] == "assets":
        return ["assets", "git", *raw[2:]]
    return raw


