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
