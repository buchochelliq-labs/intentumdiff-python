# Binding inventory (issue #338)

This package must be a thin binding over intentumdiff-core: C-ABI transport, DTO
marshalling and error translation (`AGENTS.md`). This page classifies every module it
ships today and names where the remaining behaviour goes. The machine-readable copy,
`tests/unit/binding_inventory.json`, is enforced by `tests/unit/test_binding_surface.py`:
no unclassified module, no new behaviour module, and no behaviour module may grow.

**Behaviour still shipped:** 51 modules, 20,082 lines (was 52 modules, 21,358 lines at
the audit; slices Q, S1, S2, the core #298 binding, the 0.0.3 ownership fixes #297/#298 and
the core guardrail verdict #296 and the 0.0.3 P2/P3 fixes removed 1,276). The budget only goes down.

## Binding (allowed)

- C-ABI transport and error translation: `intentumdiff_rust_core/__init__.py`, `intentumdiff_rust_core/ffi.py`, `rust_core.py`
- thin adapter over one core operation: `analysis/compile_commands.py`, `analysis/cross_file.py`, `api.py`, `assets.py`, `content_type.py`, `core/config.py`, `lsp_server/_codelens.py`, `lsp_server/_diagnostics.py`, `sources/patch_source.py`

## DTOs and exceptions (allowed)

- public data models and exceptions: `core/models.py`, `lsp/exceptions.py`, `plugins/exceptions.py`

## Packaging (allowed)

- package markers and re-exports: `__init__.py`, `__main__.py`, `analysis/__init__.py`, `cache/__init__.py`, `core/__init__.py`, `lsp/__init__.py`, `lsp_server/__init__.py`, `plugins/__init__.py`, `serve/__init__.py`, `sources/__init__.py`, `vcs/__init__.py`

## Behaviour to migrate or remove

| Moves to | Modules | Lines |
|---|---|---:|
| core parser_registry.rs and registry validators | `plugins/adapter.py`, `plugins/builtins.py`, `plugins/hub.py`, `plugins/language_metadata.py`, `plugins/loader.py`, `plugins/registry.py`, `plugins/registry_schema.py` | 4,798 |
| native intentumdiff CLI (core crates/cli); Python console script to become a launcher | `cli/__init__.py`, `cli/_commands.py`, `cli/_parser.py`, `cli/_shared.py` | 4,199 |
| core review API (api::review_sources / review_text); keep only DTO marshalling | `_differ_gate.py`, `_differ_presentation.py`, `_differ_runtime.py`, `differ.py` | 1,994 |
| core lsp_enrich.rs, or a product shell outside this binding | `lsp/client.py`, `lsp/config.py`, `lsp/enricher.py`, `lsp/launcher.py`, `lsp/servers.py`, `lsp_server/_handlers.py`, `lsp_server/server.py` | 1,859 |
| core commit_plumbing.rs / index-engine | `core/commit_differ.py`, `core/diffignore.py`, `core/index.py`, `core/indexer.py` | 1,489 |
| core live_server.rs + native intentumdiff-live-server | `live_server.py` | 1,477 |
| core git_source.rs / vcs_backend.rs | `sources/base.py`, `sources/file_source.py`, `sources/git_source.py`, `sources/live_buffer_source.py`, `sources/string_source.py`, `vcs/_safe_path.py`, `vcs/base.py`, `vcs/git_backend.py`, `vcs/git_cli.py`, `vcs/hg_backend.py`, `vcs/perforce_backend.py`, `vcs/svn_backend.py` | 1,247 |
| product shell outside this binding (designer decision) | `github_app.py`, `serve/_app.py`, `testing.py`, `watcher.py` | 1,200 |
| core schema resolution (#307) | `analysis/schema_resolver.py`, `analysis/user_schemas.py` | 761 |
| core guardrail_policy.rs (evaluation already Rust; reporting/orchestration remain) | `analysis/guardrail_reports.py`, `analysis/guardrails.py` | 493 |
| core cache_store.rs | `cache/duckdb_store.py`, `cache/sqlite_store.py`, `cache/store.py` | 419 |
| core diagnostics trace (#54) | `analysis/diagnostics.py` | 146 |

## Core counterparts

Core already has surfaces for most of these: `api.rs` (`review_text`, `review_sources`),
`live_server.rs`, `commit_plumbing.rs`, `git_source.rs`, `vcs_backend.rs`, `cache_store.rs`,
`parser_registry.rs`, `guardrail_policy.rs`, `lsp_enrich.rs`, `schema_context.rs` and the
native CLI in `crates/cli`. Each migration slice must first confirm the core operation covers
the Python module's public behaviour, list any missing operation as a core issue, and add
parity tests at the public API boundary before the Python code is deleted.

## How to use the gate

- Removing or shrinking a behaviour module: lower its `max_lines`, or delete its entry, in the
  same change.
- Adding a module: classify it as `binding`, `dto` or `packaging`. A new `behavior` module
  fails the gate; that code belongs in core.
- Raising a budget needs the designer's explicit direction.

