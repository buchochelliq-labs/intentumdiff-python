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
match the candidate being published. These CI wheels are built with the workflow's
development profile; they are not a substitute for verifying the eventual release
wheels. Media provenance must identify the exact installed wheel used for recording.
