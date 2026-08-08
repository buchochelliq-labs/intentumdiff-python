# Contributing

Developer setup for the IntentumDiff Python binding. User documentation
lives at https://buchochelliq-labs.github.io/intentumdiff-docs/.

## Architecture

All diffing, matching, classification, guardrails and presentation live in the engine
([intentumdiff-core](https://github.com/buchochelliq-labs/intentumdiff-core), Rust). This
package is the public Python API, CLI and VCS/config orchestration: it loads the engine's
cdylib via ctypes and drives the stable C ABI (`intentumdiff_call`). The binding does zero
functional work.

Work targets the release-candidate branch, never `main` — see the `intentumdiff-release`
skill.

## Building from source

The wheel bundles two provisioned build inputs — the engine and the parser components:

```bash
python scripts/provision_build_inputs.py \
    --core-dir /path/to/intentumdiff-core \      # or let it clone the repo
    --wasm-dir /path/to/built/components       # built by the parser repos
pip install -e .[dev]
```

Released wheels on PyPI are pre-built per platform — installing from source is a
development workflow, not the user path.

## Tests

```bash
python -m pytest tests/unit -q
```

The suite runs entirely over the ctypes path (there is no other path — the engine has
no Python dependency and this package has no engine).

## Provenance

Migrated files-only (no history) from the IntentumDiff monorepo
(`buchochelliq-labs/intentumdiff`), which remains the archive of record.

License: MIT.
