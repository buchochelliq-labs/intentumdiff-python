# Contributing to IntentumDiff (Python)

Thanks for looking. This page covers what the project is, how this repository fits into it,
and how to get a change merged.

**User documentation:** https://buchochelliq-labs.github.io/intentumdiff-docs/

## What IntentumDiff is

A textual diff compares characters, so it cannot tell a rename from a rewrite. IntentumDiff
parses both sides of a change into syntax trees, matches the trees against each other, and
classifies every difference by what it does — **meaningful**, **refactoring**, **moved** or
**ignored style**. A 400-line reformat with one behavioural change inside it becomes one
change worth reading.

## How the project fits together

IntentumDiff is deliberately split so that one implementation serves every front end.

| Repository | Role |
|---|---|
| [intentumdiff-core](https://github.com/buchochelliq-labs/intentumdiff-core) | **The engine.** Rust. Parsing, matching, classification, guardrails, presentation |
| **intentumdiff-python** (here) | The Python API, CLI and VCS/config orchestration |
| [intentumdiff-vscode](https://github.com/buchochelliq-labs/intentumdiff-vscode) | The VS Code extension |
| [intentumdiff-ast](https://github.com/buchochelliq-labs/intentumdiff-ast) | The canonical AST vocabulary — categories and roles |
| [intentumdiff-plugin-sdk](https://github.com/buchochelliq-labs/intentumdiff-plugin-sdk) | The SDK language parsers are built against |
| 78 `*-parser` repositories | One WebAssembly parser per language |
| [intentumdiff-docs](https://github.com/buchochelliq-labs/intentumdiff-docs) | This documentation site |

This package loads the engine's cdylib via ctypes and drives the stable C ABI
(`intentumdiff_call`). **It does zero functional work.**

That matters when deciding where a change belongs: if a Go or Java binding would need the
behaviour too, it belongs in the Rust core, not here. New processing logic added to Python
has to be written again for every other binding, so it goes in the engine.

## Where a change belongs

| Change | Repository |
|---|---|
| Diff, matching or classification behaviour | `intentumdiff-core` |
| Python API shape, CLI arguments, packaging | here |
| Editor behaviour | `intentumdiff-vscode` |
| Support for a language | that language's parser repo |
| Anything a reader sees | `intentumdiff-docs` |

## Making a change

**Branch from the release candidate, not `main`.** `main` is what gets tagged and published,
so every merge into it would be a potential release.

```bash
git fetch origin
git checkout -b feat/my-change origin/release/v0.0.2-rc
```

Then open the pull request against the same branch:

```bash
gh pr create --base release/v0.0.2-rc
```

Approvals are set to zero because this is a solo-maintainer project, but checks must pass.

## What a good pull request looks like

- **A test that fails without the change.** Green CI is not evidence a product works —
  every 0.0.1 defect passed CI and was obvious within thirty seconds of installing the wheel
- **Documentation updated in the same change** if a reader would notice the difference
- **Examples that actually run.** Every Python example in the docs is executed in CI against
  a real installed wheel and compared with its documented output
- Commit messages that say *why*, not just *what*

## Reporting a bug

Open an issue with your OS, `python --version` and `intentumdiff --version`. If something in
the documentation turned out not to be true, that is a bug worth reporting on its own — docs
rot is why 0.0.1 had to be pulled.

Security issues should go through the repository security policy, privately, rather than a
public issue.

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
