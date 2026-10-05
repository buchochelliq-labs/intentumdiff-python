# Host-utils FullParse guest fixture

This is a test-only Wasm component built from the immutable core commit recorded
in `provenance.json`. It imports the canonical `intentdiff:plugin/host-utils@1.0.0`
interface and delegates all tree processing to the host. It is not shipped as a
product parser.

The compressed component is checked in so Python tests require neither the old
monorepo nor a core source checkout. Tests verify the compressed bytes, expanded
Wasm and corpus hashes before loading it. They still require the provisioned Rust
C ABI library, exactly like the rest of the semantic runtime suite.

To refresh, build the recorded source with the recorded Rust toolchain and locked
command, gzip with `mtime=0`, copy the core `host_utils.json` corpus to `cases.json`,
and update all provenance hashes. Do not update expected outputs from test results.

Expectations come from the documented tree contract: removing a trivia root yields
JSON null; removing a comment child preserves its sibling; equivalent CST and
SemanticNode leaves hash the UTF-8 string `identifier:café` using SHA-256. Both the
direct Rust-backed callbacks and real guest calls must satisfy these expectations.
Malformed inputs and limit violations must raise, including when the guest tries
to ignore an import error and return an empty object.
