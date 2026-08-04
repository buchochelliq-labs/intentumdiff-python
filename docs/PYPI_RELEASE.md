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
