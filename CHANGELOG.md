# Changelog

## v0.0.1

First stable release.

- Promotes the `0.0.1b1` beta unchanged in behaviour; the version is the change.
- `__version__` now resolves the `intentdiff-python` distribution. It previously
  looked up `intentdiff`, which finds no metadata in a release install, so the
  reported version was pinned to a literal regardless of what was installed.

## v0.0.1b1 — 2026-07-27

Initial import from the IntentDiff monorepo (files-only; the monorepo remains the archive of
record). The thin Python binding + `intentdiff` PyPI package: public API, CLI, and ecosystem
glue over the engine's C ABI via ctypes. Full unit suite green (2182 passed) against a
provisioned engine build.
