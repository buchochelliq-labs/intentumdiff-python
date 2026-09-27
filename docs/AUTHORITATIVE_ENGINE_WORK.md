# Remaining authoritative-engine work

Rust is the implementation authority for shared processing. Python is an idiomatic
public API, DTO/transport layer and host integration. Source examples independently
check Rust's correctness; agreement between bindings is not sufficient.

The merged migration at Python 9eb3b7c/core 449d233 completed bounded schema,
Markdown, lifecycle, compile-context and host utility moves. It did not finish
all active-path ownership. The current issue inventory and dependency order are
maintained in buchochelliq-labs/intentumdiff-core#43:

1. Python #59: required engine failures must not become empty successful results.
2. Core #41: shared guardrail/config meaning and complete policy application.
3. Core #103: complete finalization, reconciliation and semantic flags.
4. Core #104: symbol reference resolution.
5. Core #106: remove LSP semantic target-selection fallback.
6. Core #105: patch reconstruction and validation.
7. Core #107: scoped ignore matching and commit content classification.
8. Core #108: parser selection and fallback precedence.
9. Core #39/#40/#42: invariance catalogue, trust/install policy, native CLI parity.
10. Core #98/#101: actual guest-import coverage and whole-path correctness gates.

Python #54/#57 remain umbrella acceptance issues. Dead comparison helpers are
not active fallback evidence; remove them only after callers and compatibility
are checked. Keep maturin/cffi, the shared C ABI and both public APIs. VS Code
currently uses an external Python/CLI runtime.

## First explicit-failure slice (Python #59)

Required symbol/reference extraction, cross-file comparison and guardrail
rule evaluation now raise RuntimeError naming the Rust operation on load,
missing-handler, malformed-result, wrong-result-shape or engine errors. Exception
chaining retains the cause. Historical try_rust function names remain compatible;
they no longer return None for these required operations. Successful empty results
remain valid. No error is reinterpreted by a Python semantic engine.

SemanticIndex.build validates both result DTOs before publishing either table.
A failed fresh build stays unbuilt; a failed rebuild preserves its prior snapshot.

Validation: the minimized adapter/atomicity tests reproduced 21 failures before
the fix (4 successful-empty controls passed); all 28 regressions including public
cross-file and guardrail application pass after it. The focused real-library suite
passed 148 tests with all 73 parser/renderer components staged. Independent review approved the four-adapter/atomic-index change. Its further
finding on commit indexing produced two additional failing regressions; parser
engine errors now propagate there too (explicit unsupported-parser results stay
distinct). The expanded suite passes. Fresh cross-platform CI is required before
readiness. This slice does not
claim the remaining issue inventory is complete.
