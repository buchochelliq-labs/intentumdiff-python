# Diagnostics trace

Opt-in review-quality debugging: enable `DiffConfig.diagnostics` or run the CLI with
`--diagnostics` and `SemanticDiff.metadata["diagnostics"]` carries a versioned trace —
parser selection, matching augmentation, profile anchoring, candidate accept/reject,
refinement, refactoring, invariance, presentation, and final classification, with per-stage
event counts. Normal diff output is unchanged when disabled.

The per-stage events are recorded in the shell; the underlying pass data comes from the
engine's finalize trace, so the trace reflects what actually ran.
