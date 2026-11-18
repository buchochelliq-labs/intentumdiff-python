# PyPI release runbook

- **Native wheels only — no sdist** (installing from source needs Rust + wasm toolchains; the
  opposite of the intended user experience).
- Publishing rides `publish.yml` on `v*.*.*` tags via PyPI **Trusted Publishing** (configure
  the publisher before the first tag). A `workflow_dispatch` lane rehearses against TestPyPI
  (bump the rehearsal version if TestPyPI has tombstoned filenames).
- **Size budget** (enforced by `scripts/verify_intentumdiff_wheel.py` before upload and again
  before publication): single wheel ≤ 75 MB; full release set ≤ 250 MB. Raising a cap is a
  release-planning decision, never a silent edit.
- Each wheel bundles the engine cdylib + the parser component set for its platform; the
  verifier also checks module/wasm counts and the expected version.
- Checksum artifacts are recorded per release; attestations are enabled once the repository
  visibility/org plan supports them.

## Candidate evidence before publication

Maintainer PR CI retains `candidate-evidence-<platform>-<attempt>` artifacts for 30 days
on each of the four wheel platforms. They contain the tested wheel and its SHA-256,
the tested Python checkout and core commits, Python/Rust versions, verified component
provenance, full-suite JUnit results, parity and installed-wheel smoke logs, and actual native
Rust/Python corpus inputs and outputs (`migration-parity.json`). The Python commit
is the checkout actually tested, which can be GitHub's PR merge commit.

Uploads also run after failures to preserve available diagnostics. An artifact's
presence is not a passing certification: check the corresponding workflow job and
all required steps. A failed early step can leave partial evidence. Fork and
Dependabot runs without provisioned components do not produce this full evidence set.

Before release, inspect source examples and both implementations' actual output,
verify the wheel checksum after download, and retain the approved evidence with the
release record before CI artifacts expire. Confirm the tested commits and components
match the candidate being published. These CI wheels are built with the release profile. Verify their recorded build
profile and exact source/component identities; rebuilt publication wheels still
require checksum, packaging and installed-wheel verification. Media provenance must identify the exact installed wheel used for recording.

## Before tagging: point publish at the reviewed run

`publish.yml` promotes the four wheels from one successful `CI` run, triggered by a push
to the release branch derived from the version (`0.0.3b1` → `release/v0.0.3-rc`). After
the RC CI run you intend to ship has passed on all four platforms, set these repository
variables to it (or pass the same values as `workflow_dispatch` inputs for a dry run):

- `INTENTUMDIFF_PYPI_RUN_ID`: the CI run ID.
- `INTENTUMDIFF_PYPI_SOURCE_SHA`: that run's 40-hex head commit. The tag's tree must equal
  this commit's tree, or publish fails closed.
- `INTENTUMDIFF_PYPI_CORE_SHA`: the 40-hex core commit the wheels were built from (`CORE_REF`
  in `scripts/provision_build_inputs.py`).
- `INTENTUMDIFF_PYPI_WHEELS_JSON`: one entry per runner, mapping it to that run's
  `candidate-evidence-<runner>-<attempt>` artifact ID and the wheel's lowercase SHA-256:

  ```json
  {"ubuntu-latest": {"id": 0, "sha256": "…"}, "macos-latest": {"id": 0, "sha256": "…"},
   "windows-latest": {"id": 0, "sha256": "…"}, "windows-11-arm": {"id": 0, "sha256": "…"}}
  ```

The artifacts expire 30 days after the run: tag before then. Run `publish.yml` by
`workflow_dispatch` with `dry_run: true` first.

**Trusted publisher check.** Publishing uses PyPI Trusted Publishing, with no API token.
Before the tag, confirm on PyPI (project `intentumdiff-python` → *Publishing*) that the
trusted publisher is owner `buchochelliq-labs`, repository `intentumdiff-python`, workflow
`publish.yml`, environment `pypi`, and that the GitHub `pypi` environment exists with its
protection rules. Do the same on TestPyPI with environment `testpypi` before a rehearsal.
Confirm who can manage the PyPI project, because the same people own the rollback below.

## Rollback

PyPI cannot delete a release in a way that frees its number, and never lets a version or
filename be reused. A rollback is therefore **yank, then supersede**, never *replace*.

1. **Yank the broken version** on PyPI (project → *Releases* → the version → *Options* →
   *Yank*), with a reason that names the problem and the version that supersedes it, for
   example "Incorrect diffs for X; use 0.0.3b2". A yanked version is skipped by
   `pip install intentumdiff-python` and `--pre`, but is still installed by an exact pin
   (`==0.0.3b1`), so existing lockfiles keep working.
2. **Publish a superseding version** through the normal RC path: bump `pyproject.toml`
   (for example `0.0.3b1` → `0.0.3b2`), merge to the RC, let CI build and test it, set the
   variables above to the new run, and tag. Never retag or reuse the broken number.
3. **Announce it.** Edit the GitHub release for the broken tag to state the problem, that
   the version is yanked, and which version supersedes it, and add the same note to
   `CHANGELOG.md` under the broken version. If the VS Code extension's documented runtime
   was the broken version, update its README too.

Deleting a release on PyPI is not a rollback: it breaks exact pins, and the version
number stays burned anyway. Reserve it for a release that must not be installable at all
(for example, a leaked secret).
