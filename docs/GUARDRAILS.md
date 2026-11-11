# Protected config guardrails

Guardrails pin important semantic configuration changes to the top of a diff so sensitive
config drift is never buried under ordinary edits.

Configure them in a project-root `intentumdiff.yaml`. The policy file is protected by default —
edits to `intentumdiff.yaml` itself always produce an immutable guardrail warning. The same file
may carry diff defaults under `config:` (similarity thresholds, style handling, plugin fuel,
CST byte caps); the guardrail loader ignores that section when evaluating protected paths.

## Supported languages (v1)

Profile-backed configuration languages with stable semantic identity: `json`, `yaml`, `adf`,
`databricks`, `databricks-workflow`, `hcl`, `dockerfile`, `puppet`. Protected rules match
semantic paths (not line numbers); evaluation happens in the engine.

Surfaces: pinned guardrail entries in diffs/reviews, `intentumdiff guardrails` in the CLI
(`--strict` exits non-zero), checks annotations + SARIF in the PR Action
([GITHUB_PR_REVIEW.md](GITHUB_PR_REVIEW.md)).

## Check verdict and `--strict`

The pass/fail verdict and the counts come from core's `guardrail_check_result` operation; the
Python package only marshals the violations to it and validates the result. The rule is
`passed = not strict or no violations`: under `--strict` (and `--guardrails-strict`) **any**
violation, `important` or `immutable`, fails the check with exit code 2. Without strict the check
is advisory and always passes. Before 0.0.3 the Python CLI failed a strict check only on an
`immutable` violation, which disagreed with core's native CLI.
