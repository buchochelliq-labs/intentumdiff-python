# Agent instructions — intentumdiff (Python)

The **thin Python binding** + PyPI package. Zero functional work here.

## Mandatory preflight

Before doing any work in this repository, read this file and
`docs/ARCHITECTURE.md`. Before proposing or making a Python change, confirm that
it stays within the thin-binding boundary below.

## Designer’s architecture rule

Python is only a thin binding over `intentumdiff-core`. Anything beyond the
minimal binding responsibilities is prohibited in the Python library unless
the project designer explicitly directs that specific exception.

Python may provide the public API and DTO marshalling, C-ABI transport,
host I/O, and presentation/orchestration. It must not implement semantic,
domain, policy, classification, parsing, matching, or fallback behavior.
Shared behavior and decisions belong in `intentumdiff-core` and must be
exposed through the core API. If a requested change needs such behavior, change
or extend core and keep the Python adapter minimal. Never add Python fallback
logic.

## Hard invariants

- Semantic logic belongs in intentumdiff-core — never add engine behavior to this shell.
- The suite's **skip ratchet** (`tests/unit/test_skip_ratchet.py`) fails on unclassified skip
  reasons: classify in `skip_reasons_baseline.json` or fix — never accumulate.
- There is no Python fallback engine; a missing cdylib fails loudly.

## Build + test (Python 3.12, Rust 1.95.0)
```bash
python scripts/provision_build_inputs.py --core-dir /path/to/intentumdiff-core --wasm-dir /path/to/components
python -m pip install cffi
python -m pip install -e ".[dev,serve]"
python -m pytest tests/unit -q
```
Backend check: `type(_load_backend()).__name__ == "_CtypesBackend"`.

## Map
`docs/ARCHITECTURE.md` · `docs/BUILDING.md` · `docs/USAGE.md` · `docs/GUARDRAILS.md` ·
`docs/PYPI_RELEASE.md` (runbook) · `.claude/skills/`.
