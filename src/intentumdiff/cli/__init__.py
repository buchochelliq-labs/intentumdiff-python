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

from typing import NoReturn

from intentumdiff.cli._commands import (  # noqa: F401
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
    _diagnostics_store,
    _emit_rows,
    _GitPositionalsAction,
    _human_duration,
    _parse_date_arg,
    _parse_megapixels,
    _print_index_result,
)
from intentumdiff.cli._parser import (  # noqa: F401
    _build_parser,
    _legacy_click_main,
    _normalize_argv,
)
from intentumdiff.cli._shared import (  # noqa: F401
    _MACHINE_OUTPUT_FORMATS,
    _MACHINE_PROTOCOL_COMMANDS,
    _NO_BANNER_ENV,
    _asset_options_from_args,
    _banner_disabled_by_env,
    _cmd_shell,
    _console,
    _differ,
    _emit_asset_payload,
    _emit_guardrail_reports,
    _emit_phase_profiles,
    _err,
    _exit_if_guardrail_result_fails,
    _exit_if_guardrails_strict,
    _guardrail_result,
    _has_immutable_guardrail,
    _is_machine_output,
    _json_default,
    _load_cli_config,
    _maybe_render_cli_banner,
    _parse_fuel,
    _print_asset_result,
    _print_shell_help,
    _record_diagnostics_db,
    _render,
    _render_cli_banner,
    _render_terminal,
    _render_with_profile,
    _resolve_repo_root,
    _run_parsed_command,
    _run_shell_line,
    _should_show_cli_banner,
    _version_text,
    _warn_if_unlimited_fuel,
    _write_output,
)

__all__ = [
    "_GitPositionalsAction",
    "_MACHINE_OUTPUT_FORMATS",
    "_MACHINE_PROTOCOL_COMMANDS",
    "_NO_BANNER_ENV",
    "_add_asset_diff_args",
    "_add_diagnostics_arg",
    "_add_diagnostics_db_arg",
    "_add_fuel_arg",
    "_add_guardrail_args",
    "_add_output_args",
    "_add_profile_phases_arg",
    "_add_resolve_references_arg",
    "_asset_options_from_args",
    "_banner_disabled_by_env",
    "_build_parser",
    "_cmd_assets_diff",
    "_cmd_assets_git",
    "_cmd_cache_clear",
    "_cmd_cache_export",
    "_cmd_cache_inspect",
    "_cmd_cache_list",
    "_cmd_cache_stats",
    "_cmd_diagnostics_hotspots",
    "_cmd_diagnostics_query",
    "_cmd_diagnostics_summary",
    "_cmd_file",
    "_cmd_gist_diff",
    "_cmd_git",
    "_cmd_github_pr",
    "_cmd_guardrails_check",
    "_cmd_index",
    "_cmd_live_server",
    "_cmd_lsp_server",
    "_cmd_patch",
    "_cmd_plugins_add",
    "_cmd_plugins_install",
    "_cmd_plugins_list",
    "_cmd_plugins_remove",
    "_cmd_security_check",
    "_cmd_serve",
    "_cmd_shell",
    "_cmd_string",
    "_cmd_watch",
    "_console",
    "_diagnostics_store",
    "_differ",
    "_emit_asset_payload",
    "_emit_guardrail_reports",
    "_emit_phase_profiles",
    "_emit_rows",
    "_err",
    "_exit_if_guardrail_result_fails",
    "_exit_if_guardrails_strict",
    "_guardrail_result",
    "_has_immutable_guardrail",
    "_human_duration",
    "_is_machine_output",
    "_json_default",
    "_legacy_click_main",
    "_load_cli_config",
    "_maybe_render_cli_banner",
    "_normalize_argv",
    "_parse_date_arg",
    "_parse_fuel",
    "_parse_megapixels",
    "_print_asset_result",
    "_print_index_result",
    "_print_shell_help",
    "_record_diagnostics_db",
    "_render",
    "_render_cli_banner",
    "_render_terminal",
    "_render_with_profile",
    "_resolve_repo_root",
    "_run_parsed_command",
    "_run_shell_line",
    "_should_show_cli_banner",
    "_version_text",
    "_warn_if_unlimited_fuel",
    "_write_output",
    "main",
]


#: The pre-rebrand distribution. Installing IntentumDiff does not remove it, because pip
#: treats a renamed project as an unrelated package.
_RETIRED_DISTRIBUTION = "intentdiff"


def _warn_if_retired_distribution_installed() -> None:
    """
    Warn when the pre-rebrand ``intentdiff`` distribution is installed alongside this one.

    Both projects install the same ``intentumdiff`` import package, so whichever pip laid down
    last wins and the resolved distribution name may be the retired one — which is NOT in the
    first-party trust allowlist. Every bundled parser is then rejected as untrusted third-party
    code, and the user sees a stream of ``native_fallback`` errors and no diff at all.

    Nothing in that chain names the actual cause, and the failure is total rather than partial,
    so it reads as "the tool is broken" rather than "you have two installs". This is the same
    defect class that made 0.0.1 unusable: a package failing its own trust check because of how
    its distribution name resolves.
    """
    try:
        from importlib.metadata import distribution

        distribution(_RETIRED_DISTRIBUTION)
    except Exception:  # noqa: BLE001 - absence is the normal case, and any lookup failure is fine
        return
    _err.print(
        f"[yellow]Warning:[/yellow] the retired '{_RETIRED_DISTRIBUTION}' distribution is "
        "installed alongside IntentumDiff. They share an import package, so parsers may be "
        f"rejected as untrusted and diffs may fail. Run: pip uninstall {_RETIRED_DISTRIBUTION}"
    )


def main(argv: list[str] | None = None) -> NoReturn:
    _warn_if_retired_distribution_installed()
    _legacy_click_main(argv)


if __name__ == "__main__":
    main()
