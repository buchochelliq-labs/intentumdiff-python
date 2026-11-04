# Diagnostics trace

Opt-in review-quality debugging: enable `DiffConfig.diagnostics` or run the CLI with
`--diagnostics` and `SemanticDiff.metadata["diagnostics"]` carries a versioned trace —
parser selection, matching augmentation, profile anchoring, candidate accept/reject,
refinement, refactoring, invariance, presentation, and final classification, with per-stage
event counts. Normal diff output is unchanged when disabled.

The per-stage events are recorded in the shell; the underlying pass data comes from the
engine's finalize trace, so the trace reflects what actually ran.

When syntax is incomplete or invalid, Rust preserves exact source changes and reports
`is_fallback=true`, `metadata.engine_owner="rust"`, and
`metadata.semantic_contract="rust_source_fallback_v1"`. Source additions/deletions are
classified from the changed byte ranges; these are not claims about added/deleted
semantic declarations. For example, inserting ` changed` into an incomplete Delphi
program's string literal is an `ADDITION`, with semantic equivalence marked `unknown`.
Unchanged invalid input has no changes and is not certified as style-only.
This path also works with `INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE=1`; Python performs
no token comparison. Once valid syntax is restored, normal semantic analysis resumes.
