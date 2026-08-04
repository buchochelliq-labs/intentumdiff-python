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
