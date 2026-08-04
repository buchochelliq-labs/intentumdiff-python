# Agent instructions — intentumdiff (Python)

The **thin Python binding** + PyPI package. Zero functional work here.

## Hard invariants
- Semantic logic belongs in intentumdiff-core — never add engine behavior to this shell.
- The suite's **skip ratchet** (`tests/unit/test_skip_ratchet.py`) fails on unclassified skip
  reasons: classify in `skip_reasons_baseline.json` or fix — never accumulate.
- There is no Python fallback engine; a missing cdylib fails loudly.

## Build + test (Python 3.12, Rust 1.93.0)
```bash
python scripts/provision_build_inputs.py --core-dir <core> --wasm-dir <components>
pip install cffi && pip install -e .[dev,serve]
python -m pytest tests/unit -q
```
Backend check: `type(_load_backend()).__name__ == "_CtypesBackend"`.

## Map
`docs/ARCHITECTURE.md` · `docs/BUILDING.md` · `docs/USAGE.md` · `docs/GUARDRAILS.md` ·
`docs/PYPI_RELEASE.md` (runbook) · `.claude/skills/`.
