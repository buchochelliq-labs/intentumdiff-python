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
