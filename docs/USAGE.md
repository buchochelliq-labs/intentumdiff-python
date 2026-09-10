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
Helper extraction classification remains tracked in
[Python#45](https://github.com/buchochelliq-labs/intentumdiff-python/issues/45).
Remaining Python semantic processing is tracked in
[Python#54](https://github.com/buchochelliq-labs/intentumdiff-python/issues/54).
