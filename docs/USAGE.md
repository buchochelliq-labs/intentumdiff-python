# Using intentumdiff

## Python API

```python
from intentumdiff import SemanticDiffer

differ = SemanticDiffer()

# Strings
diff = differ.diff_strings(old_source, new_source, "example.py")
for change in diff.changes:
    print(change.change_type, change.confidence, change.description)

print(diff.is_style_only, diff.has_semantic_changes)
```

`SemanticDiff` carries typed `changes` (each with category, confidence, positions, and an
intent description), `change_groups`, and style/semantic flags. Language is detected from the
filename; pass `language_hint=` to override.

## CLI

```bash
intentumdiff git HEAD~1 HEAD          # semantic diff of a commit range
intentumdiff file old.py new.py       # two files
intentumdiff string "$OLD" "$NEW" --filename x.py
intentumdiff review                   # working-tree review
intentumdiff cache stats              # cache admin
intentumdiff plugins list             # discovered parsers
intentumdiff serve                    # local HTTP playground ([serve] extra)
```

`--json` on diff commands emits the full `SemanticDiff` for tooling.

## Configuration

Project settings live in `intentumdiff.yaml` (see the sample at the repo root): ignore rules,
guardrail-protected paths, cache location, and per-language options.

## Privacy

Analysis is fully local. The optional LLM explainer is strictly BYOK and opt-in; by default
only a privacy-safe fact sheet (counts/enums/flags — never source) would leave the machine,
and only to an endpoint you configure.
## Rename and body edits (release-candidate development)

The core#44 candidate reports an established function rename separately from edits in its
body. For example, `calc(x): return x + 1` becoming `compute(x): return x + 2` carries both
the function rename and the literal modification. Reordering executable statements inside a
renamed function must also remain visible as a meaningful change.

The accompanying acceptance tests require the updated Rust core. They exercise default native
batch execution and diagnostics through the real Wasm parser, plus JavaScript/TypeScript
parser controls. Python adds no rename implementation.

See the [core reproduction and CLI evidence](https://github.com/buchochelliq-labs/intentumdiff-core/blob/fix/rename-body-edit-evidence/docs/evidence/rename-body-edit/README.md).
The [Python#45](https://github.com/buchochelliq-labs/intentumdiff-python/issues/45) example now
also recognizes `_subtotal` extraction while retaining the cap change. Extraction is bounded:
one simple returned expression, an unambiguous helper defined before the caller, unchanged
arguments and surrounding context. Shadowed names and definition-time side effects are declined.

Decorator reorderings remain meaningful, including insertion and harmless spacing. Ambiguous
function replacements remain explicit additions/deletions rather than positional renames.

Literal enrichment and review-tree equivalence now execute in Rust through thin adapters
([Python#54](https://github.com/buchochelliq-labs/intentumdiff-python/issues/54)). Parser columns
are UTF-8 byte offsets; string/character whitespace is preserved. The active invariance
evaluator already used Rust; the old Python evaluator is not a production fallback.

CI must provision the exact candidate engine SHA. `INTENTUMDIFF_CORE_REF` accepts a commit,
branch or tag; the provisioner checks out the fetched commit detached. A moving RC branch
that predates a fix cannot validate its new wrapper tests.

### Reviewing unified patches

`PatchSource(patch_text, original_content=base)` delegates parsing and strict
application to Rust. Context/removal mismatches, inconsistent hunk ranges,
unknown gaps, binary patches and multi-file inputs raise errors.

Without original content, an ordinary patch reconstructs only its contiguous
visible excerpt. `PatchSource.is_partial` exposes that fact and `get_content()`
emits `PatchExcerptWarning`; unchanged trailing content is unknown. Use
`require_complete=True` to reject partial reconstruction, or supply the original
content (`intentumdiff patch --base ...` for the CLI). Creation/deletion headers
can establish a complete empty opposite side. An empty patch without an original
also cannot establish complete file content.

Filename inference follows the conventional `a/` old and `b/` new header pair; creation `b/` and deletion `a/` headers use the same convention. Plain headers naming the same `a/` or `b/` directory preserve it. Single-sided headers can be ambiguous: provide an explicit filename to preserve a literal prefix.

`DiffIgnore(patterns)` accepts raw ignore-file text; optional `directory_rules={"src": "*.log"}` supplies nested rules. Rule parsing and matching run in Rust. An excluded parent cannot be overridden by a child negation unless the parent is re-included first. Paths use repository-relative forward slashes and matching is case-sensitive. The former test-oriented pathspec-object constructor is replaced by raw text.

Commit review preserves the engine's deletion evidence. A simultaneous `.gitignore`
edit does not prove that it caused a tracked deletion, so Python no longer marks
such deletions as `gitignore_excluded`. The field remains available for explicit
engine evidence; ignore matching alone is not causal evidence.
