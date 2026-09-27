# intentumdiff (Python) architecture — the thin binding

Everything semantic — parsing, matching, classification, finalize, guardrails, cache, config,
VCS reads — happens in the engine
([intentumdiff-core](https://github.com/buchochelliq-labs/intentumdiff-core)). This package is the
Python skin: it does **zero functional work**.

## What lives here

- **The public API** (`SemanticDiffer`, the `SemanticDiff`/`Change` pydantic DTOs) and the
  Python CLI (`intentumdiff` console script) — orchestration and presentation only.
- **The ctypes binding** (`src/intentumdiff/rust_core.py`): `_CtypesBackend` loads the bundled
  engine cdylib and drives the
  [C ABI](https://github.com/buchochelliq-labs/intentumdiff-core/blob/main/docs/C_ABI.md) —
  `intentumdiff_call(name, json_args)` → envelope → result or a mapped exception
  (`not_found` → `FileNotFoundError`, `internal` → `RuntimeError`, else `ValueError`).
- **Ecosystem glue**: plugin discovery/entry-points, the registry client shell (validators run
  in the engine), the HTTP playground (`serve` extra), the watcher, GitHub PR helpers.

## What ships in the wheel

The wheel bundles the engine cdylib
(`intentumdiff/intentumdiff_rust_core/intentumdiff_rust_core.{dll,so,dylib}`) and the built parser
components (`intentumdiff/wasm/*.wasm`) — both **provisioned build inputs**
(`scripts/provision_build_inputs.py`), not sources of this repo. There is no Python fallback
engine: if the cdylib is absent, the package fails loudly rather than degrading.

### Incomplete code

Incomplete-code detection and fallback comparison belong to Rust (core #21).
The Python `_token_fallback_diff` name remains a compatibility adapter to
`source_fallback_diff` over the C ABI; it does not tokenize or compare sources.
Rust preserves exact source ranges, including whitespace, and labels semantic
interpretation as unknown. Raw CST errors are checked before trivia equivalence.
The same fallback is allowed by the Rust-only gate. Missing core still fails.

### Shared-operation migration

Schema provider discovery, descriptor validation/matching, identity derivation,
compile-database interpretation, Wasm host tree utilities, file lifecycle and
Markdown reconciliation are Rust-owned. Python retains schema fetching/cache I/O,
filesystem discovery, Wasm host integration and conversion to public models.
`review_text(old, new, filename=...)` returns `SemanticDiff` without loading parsers;
`SemanticDiffer.diff_strings` remains the language-aware API. Existing APIs remain
compatible. A C-ABI library is always loaded with ctypes, never imported as PyO3.
Maturin continues to build cffi wheels.

Native Rust callers use `intentumdiff_rust_core::api::{review_text, review_sources}`
with typed results/errors. Future Go/Java bindings retain the existing C ABI.
VS Code still uses an external Python/CLI runtime; this migration neither bundles
Python nor switches the extension to native Rust.

The cross-repository gate `scripts/check_migration_parity.py` consumes the pinned
core's shared corpus, checks source-derived required/forbidden results, and compares
native Rust with public Python APIs. Parser-free text review is compared with text
review, and schema-aware source review with the corresponding parser-backed review.
Execution telemetry, IDs/hashes and audit-only suppression counts are excluded from
comparison; source positions, labels, classifications, groups and flags are not.
