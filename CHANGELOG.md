# Changelog

## v0.0.3b1 — pre-release (unreleased)

A pre-release: install it with `pip install --pre intentumdiff-python`.

### Core 1c4bdbc and registry eb02686 (registry #20/#21) parser components

- `CORE_REF` pins core `1c4bdbc5dfbe6e738b3d70d8964e7f148d711821` (core #323): Windows ARM64
  admits the registry #20 PowerShell component, which its CI compiled there under metered
  Wasmtime 48.0.7 (core #323); the certified native Python route ids operator leaves by
  child index, like the component (core #321); and number respellings are proved
  equivalent (core #320).
- `REGISTRY_REF` pins registry `eb02686ba69eb8d03e751977ee3a4c9a84fae8a0` (registry #21),
  which keeps registry #20's re-pin of 41 parser components to their reviewed
  `release/v0.0.3-rc` CI builds and adds make-parser#9 (whitespace inside a make value is
  data again).
  The previous wheel staged components from registry `8911328` (#17), which predates
  registry #18/#19 and every parser fix merged since, including the complete-literal
  JS/TS/JSON/SQL/YAML components.
- With these components and core 8898e3f, the 760-case language assessment passes all
  682 oracle-judged probes (previously 591 with the stale components), with no regressions.

### Core 74618c3 (core #312) adoption

- `CORE_REF` pins core `74618c311836983b4f60ae2d8389edfdd14f7793` (merge of core #312,
  review completeness, core #252).
- `SemanticDiff` carries core's `completeness` (`"complete"` | `"equivalent"` |
  `"incomplete"`) and `incomplete` (`IncompleteReview`: `reason`, `message`, `report_url`,
  `language`, `core_version`, `source_ranges[]`, `source_ranges_truncated`,
  `source_ranges_available` and `report` = `IncompleteReport` with `title`,
  `body_markdown`, `truncated`, `issue_url` and the sanitised `outline`). Both are
  marshalled verbatim (strict types; unknown keys kept; JSON output is core's DTO) and never
  derived in Python. `IncompleteReview` and `IncompleteReport` are exported from
  `intentumdiff`. A malformed verdict (an unknown `completeness`, a verdict without the
  `incomplete` key, `incomplete` set on any other state, or a mistyped report field) raises
  the DTO translation error (`ValueError`). `completeness` is `None` only on a DTO core did
  not serve (e.g. a `SemanticDiff` built by the caller).
- Member reorders inside JSON objects, YAML mappings and TOML tables are now `equivalent`
  with `metadata.equivalence_proof = "keyed_data.unordered_mapping_member_order"`.
  `try_rust_finalize_review` previously dropped finalize's `equivalence_proof` (and the
  `suppression_trace` the bug report's outline uses) before `complete_routed_review`; both
  are now transported unchanged.
- CLI: an incomplete review shows core's `incomplete.message` verbatim (core's terminal
  panel replaces "No changes detected", and the message is repeated on stderr). On an
  interactive terminal, or with the new `--report-bug` flag on `git`, `file`, `patch` and
  `string`, the CLI prints core's `report.title` and `report.body_markdown` for preview and
  asks `[y/N]`; only an explicit `y`/`yes` calls `webbrowser.open(report.issue_url)`. When
  `report.truncated` is true it says the URL was shortened and that the printed body is the
  full report to paste. Without a TTY nothing is ever opened; the CLI prints the message and
  how to report. All of this is on stderr, so JSON and other machine output is unchanged.
- `intentumdiff watch` renders an incomplete review through core's renderer instead of
  printing "no changes".

### Core 9b46bfb (core #310) adoption

- Wasm fuel budgets (parser full-parse and renderer, API and CLI) come from core's
  `fuel_budget` operation; the Python `_fuel_budget` helper and its formulas are gone (#308).
  **Behaviour change:** core scales the configured `plugin_fuel` floor to
  `20M + 20,000 × content bytes` (the renderer used `20M + 100 × bytes`, the full-parse path
  `50M + 150,000 × the larger side`); a pair parse counts both sides' bytes. `-1` (or any
  negative `plugin_fuel`) stays unlimited and a budget below 1M is kept exactly.
- Registry manifests are validated by core's `validate_registry_manifest` operation; the
  Python field, pattern and enum checks are gone (#329). `RegistryManifestError.errors`
  carries core's `{"path", "message"}` errors (RFC 6901 paths) and `validate_registry()`
  returns them as `"<path>: <message>"`. **Behaviour change:** official entries (the default
  tier) now need an immutable 40-hex git `ref` (or an exact PyPI version), `wasm_checksums`,
  `capabilities` and `abi_target`; capabilities must be unique and one of `diff-analyzer`,
  `enricher`, `index-engine`, `parser`, `renderer`; `abi_target` must be `MAJOR.MINOR.PATCH`
  in every tier. The published registry (f95ceaa) passes. Error strings changed.
- An invalid registry manifest no longer blocks loading (#309). Core's validator still judges
  it, but entries it reports under `/plugins/<name>` are skipped and every other entry loads;
  a manifest whose top level is unusable (not a mapping, or `plugins` missing or not a
  mapping) loads no entries. Either way a `RegistryManifestWarning` (a `UserWarning`, exported
  from `intentumdiff.plugins.exceptions`) lists core's `"<path>: <message>"` errors and names
  the skipped entries; it is also logged, and `intentumdiff plugins add` prints it once on
  stderr. **Behaviour change:** `parse_registry_manifest` / `fetch_official_registry` raise
  `RegistryManifestError` only for text that is not YAML or has no JSON form. Strict
  validation is unchanged: `check_registry_manifest`, `validate_registry()` and
  `python -m intentumdiff.plugins.registry_schema` still fail on any error.
- `DiffConfig.allowed_plugins` has one rule, core's `parser_allowlist_matches`, in parser
  discovery (`language_ids`, `language_info`), actual selection and the certified Python batch
  (#311). `None` allows every parser and `[]` none; otherwise an entry must exactly equal a
  parser's grammar id, plugin id (e.g. `intentumdiff-python:python:python`), catalogue alias
  or declared language id. **Behaviour change:** plugin ids and language ids (e.g.
  `typescript`) now select their parser, and `python-parser` / `python_parser`, which only the
  batch gate accepted, select nothing anywhere.

### Core 0877c75 (core #308) adoption

- `NodeFacts` carries core's new `visibility` (`public|internal|private|protected|special|unknown`)
  and `stub_sentinel` (`pass|ellipsis|not_implemented|docstring_only|todo_macro|panic`) facts;
  `SemanticDiff` carries `content_class` (`code|docs|config|data|text`). All three are closed
  enums validated as given; Python never derives them.
- Core size-limit failures (C-ABI `error_type: "size_limit"`) raise
  `intentumdiff.CoreSizeLimitError`, a `ValueError` subclass, so existing `except ValueError`
  handlers keep working. The message names the diff-config setting to raise.

### Changed: `--strict` guardrail checks fail on any violation

- The guardrail check verdict (counts and pass/fail) now comes from core's
  `guardrail_check_result` operation, the same function the native CLI uses; the Python
  package no longer has its own copy of the rule (#296).
- **Behaviour change:** under `intentumdiff guardrails check --strict` and
  `--guardrails-strict`, an `important` violation now fails the check (exit 2), not only an
  `immutable` one. Core's rule is `passed = not strict or no violations`. Non-strict checks are
  still advisory and always pass.

### Fixed and changed in the binding and CLI shells

- `SemanticDiffer.render("terminal")` uses core's terminal renderer, the same output as the
  CLI; filenames and descriptions containing `[...]` are shown literally (#306).
- `intentumdiff git --repo REPO` reviews every changed file of another repository (#317).
  Extra positionals (#318) and conflicting `--staged` / `--unpushed` / `--new` (#319) are usage
  errors instead of being ignored.
- `plugins add/install --skip-verify` takes the `--yes` confirmation its message asks for;
  `plugins install --strict-registry`, which had no effect, is rejected (#314).
- A malformed plugin registry manifest is reported with every violation: strict validation
  raises `RegistryManifestError`, loading warns and skips the rejected entries (#309); the
  validator accepts the published `renderer_components` bundle (#330), never raises on wrong
  types and matches whole strings (#329).
- `lsp_servers.json` with two entries for one language is rejected (#312).
- `intentumdiff serve`: escaped lone surrogates get a 422 (#316), the language catalog loads off
  the event loop (#320), and `--host ::1` binds IPv6 (#323).
- Renderer fuel `-1` stays unlimited in the CLI (#308); non-finite image budgets are usage
  errors (#315); webhook signatures and PR numbers are strictly validated (#321, #322).
- Watcher: style-only saves say so (#310); a deleted file is reported, not reviewed (#326).
- Corrupt schema-cache records are cache misses (#307); LSP start/send failures release every
  resource (#313); named-pipe writes are completed or fail visibly (#327).

## v0.0.2b2 — 2026-11-05 (pre-release)

A pre-release (beta), following the release rule that a version is a pre-release until it
has been used in anger. 0.0.2b2 moves all semantic judgement into the shared Rust engine
(IntentumDiff Core).
The Python package is now a thin wrapper around it, so the CLI, the VS Code
extension and the native live-server give the same answer for the same change.
Where the engine cannot give a complete, trustworthy answer, 0.0.2 now says so
instead of guessing.

### Upgrading from 0.0.2b1

- **`intentumdiff git --format json` output changed shape**: see *CLI output
  contract* below.
- **Wheels are platform-specific**: manylinux x86-64, macOS, Windows x64 and
  Windows ARM64. Each bundles the same pinned, checksum-verified parser
  components, so no separate component download is needed.

### Fixed: changes that were reported as formatting-only

These changes alter behaviour, but 0.0.2b1 could report them as "style only" with
no changes listed. They are now reported as real changes:

- **Operators:** `a + b` → `a - b`, `<` → `>`, `and` → `or`, `is` → `is not`,
  `+=` → `-=`, across the supported languages (Core #229).
- **Iteration keywords:** `for await (…)` → `for (…)` and `for (k in o)` →
  `for (k of o)` in JavaScript and TypeScript, and an `async` comprehension
  clause in Python (`[x async for x in xs]`) (Core #237).
- **Python `async for` / `async with`** dropping `async`.

Comment-only and whitespace-only edits are still reported as formatting-only.
Follow-up fixes from the operator work: Bash assignment changes are classified
as modifications again, Dart function renames no longer report a spurious
return-statement deletion, and C# null-coalescing guards are semantic
modifications (Core #232, #233, #234).

### Fixed: reliability ("fail closed")

When the engine's answer is incomplete or malformed, IntentumDiff now reports
an error rather than a partial or silently simplified review:

- **Git input:** incomplete Git inventories, unreadable or lossily-decoded
  sources, missing baselines and truncated name-status output are rejected,
  not half-reviewed. Binary blobs are recognised before text decoding.
- **Guardrails:** a missing or broken guardrail policy fails the run instead of
  being skipped. Policies are found from the file's real repository location
  (including across moves and symlinks), not from the current directory.
- **Plugins and parsers:** a parser component or plugin that fails to load is
  an error, not a silent fallback to a different parser.
- **LSP:** a failed review clears stale semantic results instead of leaving
  them on screen.

### Security

- **GitHub App webhooks** are rejected unless signed. Previously an
  unconfigured secret accepted unsigned deliveries (#283).
- **Parser components** run with Wasm fuel metering, and a run fails if
  metering cannot be installed (#281).
- **Schema fetching** fails closed on DNS errors and refuses redirects to
  private network addresses.

### Fixed: CLI output

- `intentumdiff git` with no changes now writes valid empty JSON, SARIF and
  `--output` files instead of printing text (#280).
- Guardrail SARIF regions use correct 1-based positions (#284).
- `[staged]` labels and bracketed file names are no longer swallowed by Rich
  markup in terminal output (#285).

### Release process

- Published wheels are the exact bytes CI built and tested on all four
  platforms. They are promoted, not rebuilt from the tag (#279).

### CLI output contract

- `intentumdiff git --format json` now returns **one JSON array** for zero, one, or
  multiple changed files. The former single-file bare JSON object is replaced by
  a one-element array; consumers of this format must handle the uniform array schema.
- `intentumdiff git --output` writes all changed files rather than repeatedly
  overwriting the report with the final diff. Multi-file HTML output is one
  document with isolated renderer frames, not concatenated HTML pages.

## v0.0.2b1 — 2026-08-09 (not published to PyPI)

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
