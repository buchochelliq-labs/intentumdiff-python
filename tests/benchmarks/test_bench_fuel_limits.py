"""
tests/benchmarks/test_bench_fuel_limits.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Benchmarks and boundary tests for Wasm fuel budgets and maximum diff size.

What is Wasm fuel?
------------------
IntentDiff runs every language parser as a WebAssembly (Wasm) module
inside the Wasmtime runtime.  Wasmtime supports *fuel-based metering*: before
invoking a Wasm module a budget of "fuel" is deposited, and each Wasm
instruction executed (``i32.add``, ``call``, ``br``, ...) costs exactly one
unit.  When the budget hits zero Wasmtime traps immediately, which
IntentDiff converts into ``PluginFuelExhausted``.

Why does fuel matter?
---------------------
1. **Safety cap** -- a buggy or adversarial parser plugin cannot spin the
   process forever.  Fuel gives a deterministic worst-case CPU bound.
2. **Metering overhead** -- enabling fuel adds roughly 5-15 % overhead to Wasm
   execution because Wasmtime inserts instruction-counter decrements at every
   backward edge and indirect call.  The *size* of the budget does not affect
   speed; only whether metering is ON (any finite value) or OFF
   (``FUEL_UNLIMITED``).  ``TestFuelMeteringOverhead`` quantifies this.
3. **Tuning** -- the default budget (100 M instructions) comfortably handles
   files up to roughly 2 000 lines.  Very large files need a higher budget or
   ``FUEL_UNLIMITED``.  ``TestFuelVsFileSize`` provides the empirical lookup
   table.

Fuel vs file size (rule of thumb)
----------------------------------
The Python Wasm parser executes roughly **50 000-100 000 Wasm instructions
per line** of source code (tree-sitter tokenisation + CST construction +
serialisation to JSON).  With a 100 M default budget that gives a practical
ceiling of about 1 000-2 000 lines.  Double the budget for every doubling
of file size.

Exact values are measured by ``TestFuelVsFileSize.test_fuel_floor_matrix``
below (run with ``pytest -s`` to see the printed table).

The performance cliff
---------------------
With ``FUEL_UNLIMITED`` and no CST-size caps, diff latency grows roughly as:

  - Parse + structural hash : O(n)        n = tokens in the source file
  - Phase 1 top-down match  : O(n log n)  heapq over all nodes
  - Phase 2 bottom-up match : O(n x k)   k = same-type bucket size
  - Edit-script generation  : O(n^2) worst-case (Chawathe BFS)

In practice the cliff appears in the edit-script step once files grow
beyond ~3 000 lines.  ``TestScaleCliff`` documents this empirically by
running full diffs at exponentially increasing file sizes.

Implementation note
-------------------
All ``SemanticDiffer`` instances are created at *module level*.  This
amortises Wasmtime JIT compilation (which fires once per ``Engine`` config
at import time, not per test round) so individual test bodies are fast.
Never create a ``SemanticDiffer`` inside a benchmarked callable unless you
are explicitly measuring cold-start cost.

Run:
    pytest tests/benchmarks/test_bench_fuel_limits.py \
        --benchmark-columns=mean,stddev,rounds --benchmark-sort=name -v -s
"""

from __future__ import annotations

import pytest

from intentdiff import DiffConfig, SemanticDiffer, StringSource
from intentdiff.core.models import FUEL_UNLIMITED
from intentdiff.plugins.exceptions import PluginFuelExhausted

from tests.benchmarks.helpers import make_python_source

# ---------------------------------------------------------------------------
# Source strings -- generated once, reused across all rounds
# All kept in (old, modified) pairs; line counts are approximate.
# ---------------------------------------------------------------------------

_TINY   = ("x = 1\n",           "x = 2\n")           # ~3 lines
_F10    = (make_python_source(10),  make_python_source(10,  modified=True))  # ~165 lines
_F50    = (make_python_source(50),  make_python_source(50,  modified=True))  # ~805 lines
_F150   = (make_python_source(150), make_python_source(150, modified=True))  # ~2 405 lines
_F300   = (make_python_source(300), make_python_source(300, modified=True))  # ~4 805 lines
_F500   = (make_python_source(500), make_python_source(500, modified=True))  # ~8 005 lines

# Medium source reused for the metering-overhead comparison
_MED    = (make_python_source(65),  make_python_source(65,  modified=True))  # ~1 045 lines

# ---------------------------------------------------------------------------
# SemanticDiffer instances -- pre-created so JIT fires at import, not per test
#
# All instances use max_cst_bytes=32 MB / max_nodes=200 000 to prevent the
# Python-side CST-size guard from masking a fuel-exhaustion result.  This
# makes the fuel budget the sole constraint being tested.
# ---------------------------------------------------------------------------

_BIG = dict(max_cst_bytes=32 * 1024 * 1024, max_nodes=200_000)

_D_1M        = SemanticDiffer(DiffConfig(plugin_fuel=1_000_000,       **_BIG))
_D_10M       = SemanticDiffer(DiffConfig(plugin_fuel=10_000_000,      **_BIG))
_D_100M      = SemanticDiffer(DiffConfig(plugin_fuel=100_000_000,     **_BIG))   # default fuel
_D_1B        = SemanticDiffer(DiffConfig(plugin_fuel=1_000_000_000,   **_BIG))
_D_UNLIMITED = SemanticDiffer(DiffConfig(plugin_fuel=FUEL_UNLIMITED,  **_BIG))

# ---------------------------------------------------------------------------
# Python-parser availability probe
# ---------------------------------------------------------------------------

try:
    _D_100M.diff(StringSource(*_TINY, "probe.py"))
    _PYTHON_AVAILABLE = True
except Exception:
    _PYTHON_AVAILABLE = False

_SKIP_NO_PYTHON = pytest.mark.skipif(
    not _PYTHON_AVAILABLE,
    reason="Python Wasm parser not installed",
)


# ---------------------------------------------------------------------------
# TestFuelMeteringOverhead -- quantify the cost of fuel tracking (ON vs OFF)
# ---------------------------------------------------------------------------

class TestFuelMeteringOverhead:
    """
    Compare ``FUEL_UNLIMITED`` (metering disabled) vs the default 100 M budget
    (metering enabled) on the same ~1 045-line Python file.

    Wasmtime's fuel metering adds overhead regardless of budget size -- the
    key variable is ON vs OFF.  Any timing gap between the two benchmarks is
    purely the instruction-counting cost inserted by Wasmtime.
    """

    @_SKIP_NO_PYTHON
    def test_unlimited_fuel_medium(self, benchmark):
        """Warm diff with FUEL_UNLIMITED (~1 045-line Python, metering OFF)."""
        src = StringSource(*_MED, "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_default_fuel_100m_medium(self, benchmark):
        """Warm diff with default 100 M fuel (~1 045-line Python, metering ON)."""
        src = StringSource(*_MED, "bench.py")
        benchmark(_D_100M.diff, src)


# ---------------------------------------------------------------------------
# TestScaleCliff -- find where latency grows super-linearly (the cliff)
# ---------------------------------------------------------------------------

class TestScaleCliff:
    """
    Full diff at exponentially increasing file sizes using ``FUEL_UNLIMITED``
    and no size caps -- reveals where latency grows super-linearly.

    Two series:
    - ``test_parse_only_*``: old == new, empty edit script.  Measures pure
      parse + hash + trivial Phase-1 cost (O(n) reference line).
    - ``test_diff_*``: old != new, full pipeline including edit-script
      generation.  The widening gap between the two series shows where
      edit-script cost dominates (the cliff).

    Expected shape:
        50 fn  (~800 ln)  : both series fast, gap small
        150 fn (~2.4k ln) : diff visibly slower than parse-only
        300 fn (~4.8k ln) : diff clearly super-linear, cliff begins
        500 fn (~8.0k ln) : cliff well established
    """

    # -- Parse-only (old == new) ----------------------------------------------

    @_SKIP_NO_PYTHON
    def test_parse_only_50fn(self, benchmark):
        """Parse-only: ~805-line Python (old == new, FUEL_UNLIMITED)."""
        src = StringSource(_F50[0], _F50[0], "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_parse_only_150fn(self, benchmark):
        """Parse-only: ~2 405-line Python (old == new, FUEL_UNLIMITED)."""
        src = StringSource(_F150[0], _F150[0], "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_parse_only_300fn(self, benchmark):
        """Parse-only: ~4 805-line Python (old == new, FUEL_UNLIMITED)."""
        src = StringSource(_F300[0], _F300[0], "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_parse_only_500fn(self, benchmark):
        """Parse-only: ~8 005-line Python (old == new, FUEL_UNLIMITED)."""
        src = StringSource(_F500[0], _F500[0], "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    # -- Full diff (old != new) -----------------------------------------------

    @_SKIP_NO_PYTHON
    def test_diff_50fn(self, benchmark):
        """Full diff: ~805-line Python with renames/deletes/additions (FUEL_UNLIMITED)."""
        src = StringSource(*_F50, "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_diff_150fn(self, benchmark):
        """Full diff: ~2 405-line Python with renames/deletes/additions (FUEL_UNLIMITED)."""
        src = StringSource(*_F150, "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_diff_300fn(self, benchmark):
        """Full diff: ~4 805-line Python with renames/deletes/additions (FUEL_UNLIMITED)."""
        src = StringSource(*_F300, "bench.py")
        benchmark(_D_UNLIMITED.diff, src)

    @_SKIP_NO_PYTHON
    def test_diff_500fn(self, benchmark):
        """Full diff: ~8 005-line Python with renames/deletes/additions (FUEL_UNLIMITED)."""
        src = StringSource(*_F500, "bench.py")
        benchmark(_D_UNLIMITED.diff, src)


# ---------------------------------------------------------------------------
# TestFuelVsFileSize -- empirical lookup table: X lines -> minimum fuel
# ---------------------------------------------------------------------------

class TestFuelVsFileSize:
    """
    Non-benchmark tests that document the minimum fuel budget for each file
    size by probing all (budget x file-size) combinations.

    ``test_fuel_floor_matrix`` always passes and prints a human-readable table
    (visible with ``pytest -s``) that users can consult when tuning
    ``DiffConfig.plugin_fuel`` for their own files.

    Rule of thumb derived from this matrix:
        budget_needed ~= lines_of_source x 60_000
    Double the budget for a safety margin.  For files over ~3 000 lines
    prefer FUEL_UNLIMITED to avoid unexpected exhaustion on complex syntax.
    """

    @_SKIP_NO_PYTHON
    def test_fuel_floor_matrix(self, capsys):
        """
        Probes every (budget, file-size) pair.  Prints a reference table to
        stdout.  Always passes regardless of outcome.
        """
        _budgets = [
            ("1M",        _D_1M),
            ("10M",       _D_10M),
            ("100M",      _D_100M),
            ("1B",        _D_1B),
            ("UNLIMITED", _D_UNLIMITED),
        ]
        _files = [
            ("tiny    (~3 ln)",  *_TINY),
            ("10fn  (~165 ln)",  *_F10),
            ("50fn  (~805 ln)",  *_F50),
            ("150fn (~2.4k ln)", *_F150),
            ("300fn (~4.8k ln)", *_F300),
            ("500fn (~8.0k ln)", *_F500),
        ]

        col_w = 12
        hdr = f"{'File size':<22} | " + " | ".join(
            f"{'fuel=' + fuel_label:^{col_w}}" for fuel_label, _ in _budgets
        )
        sep = "-" * len(hdr)

        with capsys.disabled():
            print(f"\n\nFuel vs File Size  (ok = succeeded, exhaust = PluginFuelExhausted)")
            print(sep)
            print(hdr)
            print(sep)

            for label, old_src, new_src in _files:
                cells = []
                for _, d in _budgets:
                    try:
                        d.diff(StringSource(old_src, new_src, "t.py"))
                        cells.append(f"{'ok':^{col_w}}")
                    except PluginFuelExhausted:
                        cells.append(f"{'exhaust':^{col_w}}")
                    except Exception:
                        cells.append(f"{'error':^{col_w}}")
                print(f"{label:<22} | " + " | ".join(cells))

            print(sep)
            print("Rule of thumb: budget ~= lines x 60 000.  "
                  "Use FUEL_UNLIMITED above ~3 000 lines.")
            print()

    @_SKIP_NO_PYTHON
    def test_fuel_100_always_exhausts(self):
        """Budget of 100 instructions exhausts on any real parse."""
        d = SemanticDiffer(DiffConfig(plugin_fuel=100, **_BIG))
        with pytest.raises(PluginFuelExhausted):
            d.diff(StringSource(*_TINY, "t.py"))

    @_SKIP_NO_PYTHON
    def test_100m_succeeds_on_medium(self):
        """Default 100 M budget handles a ~1 045-line file."""
        _D_100M.diff(StringSource(*_MED, "bench.py"))

    @_SKIP_NO_PYTHON
    def test_unlimited_succeeds_on_large(self):
        """FUEL_UNLIMITED handles a ~8 005-line file."""
        _D_UNLIMITED.diff(StringSource(*_F500, "bench.py"))
