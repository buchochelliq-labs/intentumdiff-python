# Changelog

## v0.0.2b1 — 2026-08-09

A beta, because 0.0.1 was published without anyone installing it and using it. That release
was pulled from PyPI within a day; this one fixes what it got wrong.

### Fixed

- **Every run printed ~69 "Failed to catalog parser plugin" errors.** The package omitted its
  own distribution name from its first-party trust list, so all 78 bundled parsers were
  refused as untrusted third-party code. Results were still correct, which is why exit code 0
  said nothing was wrong.
- **`python -m intentumdiff` failed.** There was no `__main__.py`. Only the console script
  worked, despite the docs reaching for `python -m` throughout.
- **Error messages linked to pages that did not exist.** 0.0.1 pointed at an unregistered
  domain; its replacement pointed at a file that was not in the repository. Both now point at
  the documentation site, and CI follows every URL we ship so a third cannot go unnoticed.
- **The SARIF reports we emit named a repository that does not exist.** Anyone following the
  `informationUri` from a code-scanning result got a 404.
- **`intentumdiff file a.py b.py` labelled both sides with the NEW filename**, and reported a
  "working tree" scope for diffs where no working tree was involved.
- **An OSV advisory notice was printed to stderr on every invocation**, naming an
  "allow vulnerable" override. It is a debug detail, not something to act on.
- **Intent facts were computed and then thrown away.** The engine derives structural facts —
  early exits, negated conditions, guard clauses — and the Python layer dropped three of them
  at the boundary because the model did not declare them. Explanations were built from a
  smaller picture than the engine actually had.
- **Renderers could be absent and the run still looked healthy.** A missing renderer component
  was silent, so output could quietly fall back rather than say what was missing.
- **Refusing to read a file outside the workspace was logged as an internal error.** It is a
  deliberate refusal, and now reads as one instead of looking like a crash.
- **The certified engine path was never taken.** The wheel publishes as `intentumdiff-python`
  while the import package is `intentumdiff`, so the parser id arrived distribution-qualified
  and failed an allowlist that did not recognise it. Diffs were still correct and still Rust,
  so nothing looked wrong — but the certification, and the intent facts only that path
  derives, were silently missing.

### Added

- `scripts/smoke_published_wheel.py` — installs the built artefact into a clean virtualenv and
  does what a new user does in their first five minutes: install, import, console script,
  `python -m`, a real diff, **clean stderr**, and every URL in the output resolving. It exists
  because every 0.0.1 defect passed CI and was obvious within a minute of installing the wheel.
- Documentation at https://buchochelliq-labs.github.io/intentumdiff-docs/, where every example
  is executed in CI against a real installed wheel and compared with its documented output.

### Known limitations

- Requires **Python 3.12 or newer**. On older interpreters pip reports "could not find a
  version that satisfies the requirement", which reads as a missing package but is not.

## v0.0.1

First stable release.

- Promotes the `0.0.1b1` beta unchanged in behaviour; the version is the change.
- `__version__` now resolves the `intentumdiff-python` distribution. It previously
  looked up `intentumdiff`, which finds no metadata in a release install, so the
  reported version was pinned to a literal regardless of what was installed.

## v0.0.1b1 — 2026-07-27

Initial import from the IntentumDiff monorepo (files-only; the monorepo remains the archive of
record). The thin Python binding + `intentumdiff` PyPI package: public API, CLI, and ecosystem
glue over the engine's C ABI via ctypes. Full unit suite green (2182 passed) against a
provisioned engine build.
