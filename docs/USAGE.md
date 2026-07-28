# Using intentdiff

## Python API

```python
from intentdiff import SemanticDiffer

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
intentdiff git HEAD~1 HEAD          # semantic diff of a commit range
intentdiff file old.py new.py       # two files
intentdiff string "$OLD" "$NEW" --filename x.py
intentdiff review                   # working-tree review
intentdiff cache stats              # cache admin
intentdiff plugins list             # discovered parsers
intentdiff serve                    # local HTTP playground ([serve] extra)
```

`--json` on diff commands emits the full `SemanticDiff` for tooling.

## Configuration

Project settings live in `intentdiff.yaml` (see the sample at the repo root): ignore rules,
guardrail-protected paths, cache location, and per-language options.

## Privacy

Analysis is fully local. The optional LLM explainer is strictly BYOK and opt-in; by default
only a privacy-safe fact sheet (counts/enums/flags — never source) would leave the machine,
and only to an endpoint you configure.
