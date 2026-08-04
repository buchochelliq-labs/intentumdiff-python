# intentumdiff (Python)

[![CI](https://github.com/buchochelliq-labs/intentumdiff-python/actions/workflows/ci.yml/badge.svg)](https://github.com/buchochelliq-labs/intentumdiff-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

The **Python binding and `intentumdiff` PyPI package** — a thin shell over the IntentumDiff
engine. Semantic code review: detect intent, moves, refactorings, and style changes.

All diffing, matching, classification, guardrails, and presentation live in the engine
([intentumdiff-core](https://github.com/buchochelliq-labs/intentumdiff-core), Rust). This
package is the public Python API, CLI, and VCS/config orchestration: it loads the
engine's cdylib via ctypes and drives the stable C ABI (`intentumdiff_call`). The binding
does zero functional work.

```python
from intentumdiff import SemanticDiffer

diff = SemanticDiffer().diff_strings(old, new, "example.py")
for change in diff.changes:
    print(change.change_type, change.description)
```

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
