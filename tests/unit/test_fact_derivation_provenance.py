"""The fact-derivation pipeline must actually reach every entity it claims to describe.

# Why this exists

Intent facts are derived twice: once from the RAW CST (whose shape depends on which parser
produced it) and once from the NORMALISED SemanticNode (which is parser-independent). The
second pass exists to fill what the first cannot reach.

For an unknown length of time it filled nothing, and no test could see it:

- `merge_facts` used `entry(k).or_insert(v)`, which treats a key present with a **null** as
  already answered. The CST pass emits a partial bag whose unreachable facts sit as explicit
  nulls, so `behavior_category: null` permanently blocked the real value.
- Every fact still had a plausible value, so output looked fine. Only the *absence* of the
  richer values gave it away, and nothing asserted on absence.

Diagnosing it took four full rebuilds, because nothing could answer "which pass ran?". These
tests make that question answerable — and keep it answered.

They assert PROVENANCE, not values. `test_intent_facts_sufficiency` already covers values;
this covers the pipeline that produces them, so a future refactor that silently stops calling
the enrichment pass fails here loudly instead of degrading facts quietly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

# One class and one function, each with facts that only the normalised pass supplies.
SOURCE = textwrap.dedent(
    '''
    from enum import Enum

    class Colour(Enum):
        RED = 1

    def scale(value, factor=2, offset=0):
        if value < 0:
            return 0
        return value * factor + offset
    '''
).lstrip()

PROBE = '''
import json
from intentumdiff import SemanticDiffer

diff = SemanticDiffer().diff_strings("x = 1\\n", "x = 1\\n" + {src!r}, "m.py")
out = []
def walk(node):
    if node.facts is not None:
        out.append({{"type": node.node_type, "trace": node.facts.facts_trace}})
    for child in node.children:
        walk(child)
for change in diff.changes:
    node = getattr(change, "new_node", None)
    if node is not None:
        walk(node)
print("PROVENANCE" + json.dumps(out))
'''


def _traces() -> list[dict[str, str | None]]:
    """Run a diff in a subprocess with tracing on.

    A subprocess because the flag is read once and cached for the process lifetime — setting
    it after the engine has loaded would silently do nothing, which is exactly the class of
    bug this file exists to catch.
    """
    env = {**os.environ, "INTENTUMDIFF_TRACE_FACTS": "1"}
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(src=SOURCE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=300,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr[-800:]}"
    line = next(
        (ln for ln in result.stdout.splitlines() if ln.startswith("PROVENANCE")), None
    )
    assert line is not None, f"probe produced no provenance: {result.stdout[-500:]}"
    import json as _json

    return _json.loads(line[len("PROVENANCE") :])


def test_tracing_is_off_unless_asked_for() -> None:
    """The flag must be opt-in: a normal run carries no diagnostic payload."""
    from intentumdiff import SemanticDiffer

    diff = SemanticDiffer().diff_strings("x = 1\n", "x = 1\n" + SOURCE, "m.py")
    for change in diff.changes:
        node = getattr(change, "new_node", None)
        if node is not None and node.facts is not None:
            assert node.facts.facts_trace is None, (
                "facts_trace leaked into a normal run; it is diagnostic-only and must not "
                "appear unless INTENTUMDIFF_TRACE_FACTS is set."
            )


def test_the_normalised_pass_reaches_every_entity_that_has_facts() -> None:
    """The gate.

    Every node carrying facts must show the enrichment pass in its provenance. A node whose
    trace lacks ``enrich(`` was described entirely by the parser-shape-dependent CST pass, so
    whatever that pass could not reach for that parser is silently missing — which is exactly
    how `is_enum`, `recursive` and `has_error_handling` disappeared for 77 of 78 languages.
    """
    traces = _traces()
    assert traces, "no node carried facts; the fixture or the pipeline is broken"

    unreached = [t for t in traces if not (t["trace"] or "").count("enrich(")]
    assert not unreached, (
        "the normalised fact pass did not reach these nodes:\n  "
        + "\n  ".join(f"{t['type']}: trace={t['trace']!r}" for t in unreached)
        + "\n\nThey are described only by the raw-CST pass, whose coverage varies by parser."
    )


@pytest.mark.parametrize("expected", ["cst", "enrich("])
def test_the_trace_names_the_passes_that_ran(expected: str) -> None:
    """Provenance must be legible enough to act on, since users paste it into bug reports."""
    traces = _traces()
    assert any(expected in (t["trace"] or "") for t in traces), (
        f"no node reported {expected!r} in its trace; the marker changed or a pass stopped "
        f"running. Traces: {[t['trace'] for t in traces][:6]}"
    )


def test_the_trace_carries_provenance_only_never_source() -> None:
    """It is safe to paste into a bug report — pass names and counts, nothing from the code."""
    traces = _traces()
    secrets = ("Colour", "scale", "RED", "factor", "offset", "value")
    for entry in traces:
        trace = entry["trace"] or ""
        leaked = [s for s in secrets if s in trace]
        assert not leaked, f"facts_trace leaked identifiers {leaked} in {trace!r}"
