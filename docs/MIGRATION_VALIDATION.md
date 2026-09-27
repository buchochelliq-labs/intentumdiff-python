# Thin API migration validation

Depends on core commit 449d233e57de2c9ae956f484ce26ad95837d20d7, proposed in
buchochelliq-labs/intentumdiff-core#102. This branch is stacked on Python#55,
head d3fa0d0e480a74e0695d0f73a0133d2c42379b9b. Existing Python API compatibility,
maturin/cffi packaging and VS Code's external Python/CLI remain intact.

Local validation:
- Core: 317 unit tests and one external typed API test passed.
- Python final runnable suite: 2432 passed, 272 pre-existing skips, 11 xfails,
  9 deselected. Eight are suite defaults; one AF_UNIX socket test is blocked by
  sandbox PermissionError. That unchanged test remains enabled in CI.
- Fresh installed Linux development cffi wheel: all 17 shared native/Python
  corpus checks passed outside the source checkout.
- Recovered checkout and published-pin verification: 67 focused tests passed.
- Development wheel SHA256:
  20c2b038a2dece62195fe337402352925db9a04c1486c553a5646a6f354c9434.

The first full run had 15 failures: 13 subprocess import setup failures, one
pre-integration loaded-library failure, and the blocked socket test. Corrected
subprocess path/library runs passed; these failures are not omitted from the record.

Independent agent review examined actual diff outputs against source and reproduced
Unicode column and fnmatch-class defects. Minimized regressions failed before fixes,
then passed. Reviewer subsequently checked 216 fnmatch comparisons with zero
mismatches and confirmed scoped readiness for feature-PR review. Duplicate-heading
pairing may remain noisy but preserves source edits; broader engine work remains
tracked separately. See the core's docs/evidence/thin-api/actual-diffs.json.

Fresh cross-platform CI is still a publication gate. No merge/tag/release or installed
VSIX/GUI acceptance is claimed. This is a development correctness wheel, not a release.
