# Thin-binding audit and fix plan (0.0.3, issue #338)

Audit of `src/intentumdiff/` on `release/v0.0.3-rc` against the binding rule in `AGENTS.md`:
Python may only transport C-ABI calls, marshal DTOs and translate errors. Line numbers refer to
the audited revision. The module inventory and growth gate are in `BINDING_INVENTORY.md`.

## Verdict

The package is not yet thin: about 25,400 lines in 85 modules.

| Kind | Lines (approx.) |
|---|---|
| Genuine transport (ctypes/C ABI, DTO completeness checks, cache/VCS proxies) | 3,000 |
| Product/semantic decisions inside the library | 7,500 |
| Dead in production (only tests call it) | 1,350 |
| Shells (CLI, live server, serve, watcher, GitHub app, testing) with their own policy | 6,900 |

Core already exposes the complete per-file review as the C-ABI op `live_diff_contents`
(`api::review_sources`), but `differ.py` re-assembles the pipeline from about twelve
finer-grained ops, with Python-owned language allowlists and fallback choices between them.

## Correctness risks (Python can answer differently from core)

1. **[Fixed in S1]** **Language allowlist forces a fallback.** `_differ_gate.py` `RUST_FINALIZE_LANGUAGES`, used in
   `differ.py`: any parser language not listed (dbt-sql, dbt-yaml, third-party plugins) returns a
   fallback labelled `rust_core_disabled`, although core's `finalize_review` accepts any language.
2. **[Fixed in S2]** **Unrecognised `INTENTUMDIFF_RUST_CORE` value disables the engine.** `core/models.py`: any value
   outside a fixed set sets `experimental_rust_core=False`, and every file gets the fallback.
3. **Python reroutes declined batch items** (`status` candidate/fallback) through its own pipeline,
   and converts some non-OS exceptions into a decline instead of raising (`rust_core.py`).
4. **Lossy round trip between two core calls.** `try_rust_finalize_review` builds Pydantic
   `Change`/`ChangeGroup` objects (`extra=ignore`) and re-serialises them for
   `complete_routed_review`; undeclared Rust fields are dropped, new group kinds fail.
5. **Python rewrites engine output:** stamps `engine_owner="rust"` when missing, overwrites
   `metadata.rust_core.*`, `content_type`, `staging_status` and filenames.
6. **Streaming and non-streaming live reviews use different engines** (`live_server.py`): streaming
   uses Python `CommitDiffer.iter_file_diffs` with its own binary backstop, non-streaming uses core
   `live_handle_review`; a text file starting with `%PDF-1.4` is in one and not the other.
7. **Two implementations discover `intentumdiff.yaml`** (guardrails vs core `config.rs`) with
   different start-directory and missing-explicit-path behaviour.
8. **Two indexer binary rules** (`index_repo` vs `index_directory`).
9. **LSP builds its own source** (`LiveBufferSource`: basename filename, own git reads) instead of
   core `live_handle_diff` / `working_tree_source_content`.
10. **Guardrail pass/fail decided in Python twice** (`models.py`, `guardrail_reports.py`) and turned
    into CLI exit code 2; core has no equivalent op.
11. **Two terminal renderers and two fuel formulas** (`SemanticDiffer.render` vs the CLI's core
    `render_cli_review`; `_fuel_budget` vs the CLI's `max(fuel, 20M+len*100)`).

## Fix plan, in dependency order

**Q. Dead-code deletion (S, no core work). Done** (commit 5e12125). Commit-JSON path and helpers in `rust_core.py`,
`_diff_commit_certified_json` in `differ.py`, the fast collector in `git_source.py`,
`analysis/text_review.py`, `analysis/invariances.py`, dead parts of `guardrails.py`,
`diagnostics.py` and `_differ_presentation.py`, re-exports in `differ.py`, the dead TypeError retry.
Proof: no reference from `src`; tests that only exercised these are removed or repointed.

| # | Remove from Python | Replaced by core | Parity proof | Size |
|---|---|---|---|---|
| S1 **done** (ab7b60a) | Language gates (`_differ_gate.py` finalize allowlist, profile gating in `differ.py`) | Existing `finalize_review`, `enrich_profile_labels`, called for every language | All-language corpus plus an unknown plugin language: same output, never a fallback | S |
| S2 **done** (1cdbe08) | Env kill-switch route (`models.py`, fallback branch in `differ.py`) | Core required by policy; "core disabled" raises | Corpus under each env value: identical output or an explicit error | S |
| S3 | `differ.py` per-file orchestration, content-type/lifecycle helpers, compile-commands walk, finalize→complete round trip | Existing `live_diff_contents` (`api::review_sources`), extended for host-loaded third-party parsers, enricher/analyzer hooks, diagnostics trace and fuel policy (new core work) | `check_migration_parity.py`: `diff_strings` byte-identical to `review_sources` | L |
| S4 | Batch reroute and metadata stamping | Core emits final metadata and owns declined items | Fake backend: a canned decline surfaces unchanged, no second op | M |
| S5 | `CommitDiffer` / `diff_commit` orchestration and streaming review | `live_handle_review` for all languages plus streaming events (new core work) | Stream, non-stream and `diff_commit` give identical `CommitDiff` | L |
| S6 | `StagedSource` fallback, `LiveBufferSource` git reads, `_safe_path` duplicates | `working_tree_source_content`, new `staged_source_content`, `live_normalise_request_path` | Git fixture matrix (added, renamed, binary, staged, unstaged) | M |
| S7 | Guardrail discovery, pass/fail, SARIF, annotations | `config::find_config_path`; new `guardrail_check_result`, `render_guardrail_{sarif,annotations}` | Discovery matrix vs core; SARIF snapshot | M |
| S8 | `language_metadata.py` tables, registry alias tables | New `parser_catalog_metadata` op over `parser_manifest.json` | Detection over the manifest equals the native live server | M |
| S9 | Indexer blob listing, binary rules, Python cache key | New `list_text_blobs(repo, ref)`; existing `cache_make_key` | `index_repo` = `index_directory` = core on a mixed-binary fixture | M |
| S10 | Python terminal renderer and fuel formulas | Existing `render_cli_review`; new `fuel_budget` | `render()` output equals CLI output | S |

## Progress

- **Q done** (5e12125): the listed dead code is deleted, plus what became dead with it
  (fast collector helpers, unused adapters, re-exports); the two dead modules
  `analysis/text_review.py` and `analysis/invariances.py` are gone.
- **S1 done** (ab7b60a): `RUST_FINALIZE_LANGUAGES` is deleted; every parser language goes to
  core `finalize_review`, every tree to core `enrich_profile_labels`.
  `RUST_CERTIFIED_LANGUAGES` (the single-file certified-batch shortcut) remains, with S3.
- **S2 done** (1cdbe08): an "off" or unrecognised `INTENTUMDIFF_RUST_CORE` /
  `INTENTUMDIFF_EXPERIMENTAL_RUST_CORE` value, or `experimental_rust_core=False`, raises
  `RustCoreRequiredError`; the "rust core disabled" fallback branch is gone.
- **Core #298 binding** (184e44d): `CommitDiffer` transports core
  `commit_cross_file_index` instead of raising "semantic index incomplete" itself;
  `CommitDiff` exposes `cross_file_index_status`, `cross_file_index_complete`,
  `indexed_file_count` and `index_errors`.
- **Constraint tests 1, 2 and 5** (61e5b4e) are in `tests/unit/test_binding_passthrough.py`.
  The exact-equality passthrough assertions for `diff_strings` are `xfail(strict=True)` until
  S3/S4 land; the static check's current offenders are an allowlist that can only shrink.
  Constraint test 4 for the env switch is `tests/unit/test_rust_core_required.py`, and for an
  unknown plugin language `tests/unit/test_plugin_language_routing.py`.
- **#297 done**: `analysis/schema_resolver.py` no longer keeps `IDENTITY_FIELD_CANDIDATES`,
  `DBT_IDENTITY_FIELDS` or the built-in schema URL tables (dbt, Databricks, ADF, OpenAPI,
  Kubernetes, GitHub Actions, Azure Pipelines); provider selection and identity come from
  core `schema_profiles`, and Python keeps only the fetch/cache I/O. Tests compare core's
  selection to explicit expected URLs. `tests/unit/test_binding_ownership_ratchet.py` keeps
  the retired definitions out of the package.
- **#298 done**: `analysis/user_schemas.py` no longer keeps `BUILTIN_PROVIDER_ROOTS`; core
  `schema_profiles` "validate" owns the built-in provider roots and the collision rule, and
  Python only reads descriptor files. Tests check every core root against an explicit list
  and that a canned core rejection surfaces verbatim.
- **#301 done**: the evaluator module went with slice Q; the remaining Python copy of the
  invariance catalogue (`invariances/rules.yaml`, `rules.schema.json`, package marker) is
  deleted too. It had already drifted from core's (it still called
  `core.number_literal.canonical_value.safe` implemented and enabled). Core
  (`crates/rust-core-host/invariances`) is the only catalogue; tests check served groups
  against explicit expected rule ids.
- Still open: the core finalize `tree_too_large` decline is answered by a Python-requested
  `source_fallback_diff` (S3), and the certified-batch decline reroute (S4).

## Constraint tests (prove Python adds no decisions)

1. **Passthrough:** a fake C-ABI backend returns canned envelopes; every public entry point's
   `model_dump_json()` equals the canned DTO byte for byte, including `metadata`, filenames and
   `staging_status`.
2. **Errors surface:** every canned `{"ok": false}` raises the mapped exception; the call log never
   shows Python itself choosing `source_fallback_diff` or a second review op.
3. **Call budget:** after S3 each per-file entry point issues exactly one review op plus allowlisted
   host-I/O ops.
4. **No hidden switches:** the same calls under the `INTENTUMDIFF_RUST_CORE` /
   `INTENTUMDIFF_FORCE_RUST_CERTIFIED` / `INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE` matrix and an
   unknown plugin language pass the canned output through or raise.
5. **Static boundary check (AST):** outside designated shells, forbid language-id set literals,
   extension tuples, `hashlib`/`subprocess`/`os.getenv` off an allowlist, `except Exception` blocks
   that return a value, and `model_copy(update=...)` on `SemanticDiff`.
6. **Cross-path parity** on the shared corpus: `diff_strings`/`diff` vs `review_sources`;
   `LiveBufferSource` vs `live_handle_diff`; `iter_file_diffs`+`finalize_commit_diff` vs
   `diff_commit` vs `live_handle_review`; `index_repo` vs `index_directory`.

Entry points covered: `SemanticDiffer.{diff, diff_strings, diff_commit, diff_stream,
diff_stream_progressive, parse, render, detect_language, detect_all}`, `api.review_text`,
`CommitDiffer.{diff_commit, iter_file_diffs, finalize_commit_diff}`, `Indexer.{index_repo,
index_directory, detect_languages}`, `SemanticIndex.find_references(resolve=True)`,
`load_project_diff_config`, `find_intentumdiff_config`, guardrail result builders,
`DiffIgnore.is_ignored`, `detect_content_type`, `diff_image_assets`, `diff_git_assets`, every
source's `get_content`, `semantic_diff_to_codelens`, `semantic_diff_to_diagnostics`, and
`LiveServer` diff/review in both stream modes.
