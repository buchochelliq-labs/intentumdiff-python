# GitHub PR review

The PR-review workflow is a GitHub Action (no hosted service required; a repo-hostable App
MVP exists for webhook/check-run contract testing).

## What the Action provides

- A sticky PR summary comment (`comment: true`), checks annotations for guardrails and risky
  semantic changes, a job summary with metrics.
- Artifacts: `semantic-diff.json`, `guardrails.json`, `guardrails.sarif`, and a static
  `intentdiff-review.html` report reviewers can download for a richer view.

## Minimal workflow

```yaml
name: IntentDiff
on: [pull_request]
permissions:
  contents: read
  pull-requests: write
  security-events: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: buchochelliq-labs/intentdiff/.github/actions/semantic-diff@main
        with: {comment: true}
```

The SARIF artifact feeds code scanning; guardrail severity maps to annotation levels.
