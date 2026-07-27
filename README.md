# intentdiff (Python)

The **Python binding and `intentdiff` PyPI package** — a thin shell over the IntentDiff
engine. Semantic code review: detect intent, moves, refactorings, and style changes.

All diffing, matching, classification, guardrails, and presentation live in the engine
([intentdiff-core](https://github.com/buchochelliq-labs/intentdiff-core), Rust). This
package is the public Python API, CLI, and VCS/config orchestration: it loads the
engine's cdylib via ctypes and drives the stable C ABI (`intentdiff_call`). The binding
does zero functional work.

```python
from intentdiff import SemanticDiffer

diff = SemanticDiffer().diff_strings(old, new, "example.py")
for change in diff.changes:
    print(change.change_type, change.description)
```

## Building from source

The wheel bundles two provisioned build inputs — the engine and the parser components:

```bash
python scripts/provision_build_inputs.py \
    --core-dir /path/to/intentdiff-core \      # or let it clone the repo
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

Migrated files-only (no history) from the IntentDiff monorepo
(`buchochelliq-labs/intentdiff`), which remains the archive of record.

License: MIT.
