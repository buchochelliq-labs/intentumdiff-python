# Contributing to intentdiff (Python)

- **This is a thin binding.** Semantic logic belongs in
  [intentdiff-core](https://github.com/buchochelliq-labs/intentdiff-core) — if a change makes
  Python compute something the engine could, it's in the wrong repo.
- Set up per [docs/BUILDING.md](docs/BUILDING.md); run `python -m pytest tests/unit -q`.
- The suite enforces its own hygiene: the **skip ratchet**
  (`tests/unit/test_skip_ratchet.py` + `skip_reasons_baseline.json`) fails on any skip whose
  reason isn't classified — classify or fix, never accumulate.
- Tests asserting monorepo-only artifacts self-skip here by design (the conftest guards);
  they still run in the archive monorepo.
- Style: ruff (line length 100), mypy strict, pydantic v2 idioms.
