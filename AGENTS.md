# Agent instructions — intentumdiff (Python)

The **thin Python binding** + PyPI package. Zero functional work here.

## Mandatory preflight

Before doing any work in this repository, read this file, `docs/ARCHITECTURE.md`,
and `.claude/skills/intentumdiff-python-boundary/SKILL.md`. Before proposing or
making a Python change, follow the skill and confirm that the change stays within
the binding boundary.

## Designer’s architecture rule

Python is only a thin binding over `intentumdiff-core`. The Python library may
contain only the minimal transport needed to expose core APIs (such as C-ABI
calls, serialization/DTO marshalling, and error translation). Any behavior
beyond that boundary is prohibited unless the project designer explicitly
directs that specific exception.

Semantic, domain, policy, classification, parsing, matching, fallback,
filesystem, network, or orchestration behavior that is not strictly required
to invoke the core API belongs in core or another explicitly designated
component. If core lacks a needed operation, extend core and expose it through
the binding. Never implement a Python fallback or alternate semantic path.

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
