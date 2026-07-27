"""Finalize-pass scaling tripwire (issue #53).

Empirical baseline (2026-07-21, release core): 400 functions with 200 simultaneous
edits finalizes in ~0.5 s; 400 edits in ~1.0 s, scaling ~2.2x per doubling of the
change count (superlinear but far from the feared quadratic blowup at review
scale). This benchmark pins a GENEROUS absolute budget so a scan-per-change
regression (the change_pair_exists_drafts-in-a-loop class) surfaces as a red test
instead of a slow review panel. Budget is 10x the observed time to stay
machine-tolerant — it catches complexity regressions, not machine noise.
"""

from __future__ import annotations

import time

from intentdiff import SemanticDiffer


def _program(functions: int, modify_every: int = 0) -> str:
    parts = []
    for i in range(functions):
        value = 2 if (modify_every and i % modify_every == 0) else 1
        parts.append(f"def fn_{i}(a, b):\n    x = a + {value}\n    return x * b\n")
    return "\n".join(parts)


def test_finalize_handles_hundreds_of_drafts_within_budget() -> None:
    differ = SemanticDiffer()
    differ.diff_strings(_program(10), _program(10, 2), filename="m.py", language_hint="python")
    old, new = _program(400), _program(400, 2)
    start = time.perf_counter()
    diff = differ.diff_strings(old, new, filename="m.py", language_hint="python")
    elapsed = time.perf_counter() - start
    assert len(diff.changes) == 200
    assert elapsed < 6.0, (
        f"finalize took {elapsed:.2f}s for 200 drafts (baseline ~0.5s release) — "
        "a scan-per-change pass has likely regressed to quadratic (issue #53)"
    )
