"""Report what works on this platform, one capability at a time, and never crash doing it.

WHY THIS EXISTS
---------------
Widening CI to every platform we publish a wheel for (#9) turned windows-11-arm red in a way
that carried no information: the suite exited after ~12 seconds with no traceback, no
"collected N items", and - even with PYTHONFAULTHANDLER set - no fault handler output.

A test suite is a poor diagnostic instrument. It imports everything, collects everything and
reports at the end, so an abrupt exit destroys the evidence for all of it at once. This walks
the same ground in order, printing each result before attempting the next, so the last line
printed names the step that killed the process.

WHAT IS ALREADY ESTABLISHED (do not re-derive; each cost a CI cycle)
-------------------------------------------------------------------
  - The product WORKS on arm64: wasmtime Engine constructs, all 73 components stage, and a
    real diff returns the right answer.
  - Collection of the whole tree dies with 3221226505 = 0xC0000409.
  - Not pytest's AST rewriting (--assert=plain dies too).
  - Not stack size (a 64 MiB-stack thread dies too).
  - One test module collects fine; the tree does not.
  - Bisected to tests/unit/test_supported_language_examples.py, which crashes ALONE. Its
    pytest_generate_tests calls SemanticDiffer().supported_languages(), which reaches
    registry.language_ids(), which instantiates EVERY parser. So loading all 73 components in
    one process is what is fatal.
  - NOT memory pressure. Each plugin reserves ~4 GiB of address space (measured), but that is
    0.2% of a 64-bit process's 128 TiB, and capping it to 68 MiB per plugin did not stop the
    crash.

0xC0000409 is __fastfail, which is what Rust's abort() compiles to on Windows. So the working
hypothesis is an abort inside wasmtime while compiling or instantiating a particular component
on aarch64. This script's job is now to name that component.

A child process per component is the only way to get a name out of a fail-fast: the abort
kills the child, the parent records which one and carries on.
"""

from __future__ import annotations

import pathlib
import platform
import subprocess
import sys
import traceback

# Windows fail-fast, seen as an unsigned exit code.
CRASH_CODES = {3221226505, -1073740791}

LOAD_ONE = """
import sys
from intentumdiff.plugins.loader import load_plugin
load_plugin(sys.argv[1], 10_000_000, trusted=True)
print("loaded")
"""

LOAD_MANY = """
import sys
from intentumdiff.plugins.loader import load_plugin
keep = [load_plugin(p, 10_000_000, trusted=True) for p in sys.argv[1:]]
print("loaded", len(keep))
"""


def section(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def attempt(label: str, fn) -> bool:
    """Run fn, print what happened, and keep going regardless."""
    try:
        result = fn()
    except BaseException:  # noqa: BLE001 - keep going whatever happens; reporting is the point
        print(f"  {label}: FAILED", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        return False
    print(f"  {label}: ok{'' if result is None else f' - {result}'}", flush=True)
    return True


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    section("interpreter")
    print(f"  {sys.version}", flush=True)
    print(f"  machine={platform.machine()}  platform={platform.platform()}", flush=True)

    section("native runtime")

    def _wasmtime():
        import wasmtime

        return f"{getattr(wasmtime, '__version__', '?')} at {wasmtime.__file__}"

    attempt("import wasmtime", _wasmtime)

    section("package")

    def _import():
        import intentumdiff

        return f"{intentumdiff.__version__} at {intentumdiff.__file__}"

    if not attempt("import intentumdiff", _import):
        return 0

    import intentumdiff as pkg

    wasm_dir = pathlib.Path(pkg.__file__).parent / "wasm"
    components = sorted(wasm_dir.glob("*.wasm"))
    print(f"  {len(components)} components in {wasm_dir}", flush=True)
    if not components:
        print("  nothing staged - provisioning did not run", flush=True)
        return 0

    section("does each component load ON ITS OWN?")
    crashed: list[str] = []
    other: list[tuple[str, int, str]] = []
    loaded = 0
    for c in components:
        r = run([sys.executable, "-c", LOAD_ONE, str(c)])
        if r.returncode == 0:
            loaded += 1
        elif r.returncode in CRASH_CODES:
            crashed.append(c.name)
            print(f"  ABORTS ALONE: {c.name}", flush=True)
        else:
            last = ((r.stderr or "").strip().splitlines() or [""])[-1]
            other.append((c.name, r.returncode, last))
            print(f"  fails ({r.returncode}): {c.name}  {last[:100]}", flush=True)

    print(f"\n  loaded cleanly {loaded}, aborted {len(crashed)}, other failures {len(other)}", flush=True)

    if crashed:
        section("verdict")
        print("  A SPECIFIC COMPONENT aborts on this platform:", flush=True)
        for name in crashed:
            print(f"    {name}", flush=True)

        # A Rust abort usually prints a panic line before __fastfail. Capture it: that text
        # names the wasmtime bug, which is the difference between "a component aborts" and
        # "wasmtime's aarch64 backend panics in X".
        section("what does it say before it dies?")
        target = next(c for c in components if c.name == crashed[0])
        r = run([sys.executable, "-c", LOAD_ONE, str(target)])
        print(f"  rc={r.returncode}", flush=True)
        for stream, text in (("out", r.stdout), ("err", r.stderr)):
            for line in (text or "").strip().splitlines():
                print(f"  {stream}| {line}", flush=True)
        if not (r.stdout or "").strip() and not (r.stderr or "").strip():
            print("  (silent - the abort produced no message at all)", flush=True)

        # Narrow WHICH part of codegen trips. Each variant is one child process; whichever
        # survives both identifies the trigger and is a candidate workaround.
        section("does any wasmtime setting avoid it?")
        VARIANTS = [
            ("default", ""),
            ("opt_level=none", "c.cranelift_opt_level = 'none'"),
            ("opt_level=speed", "c.cranelift_opt_level = 'speed'"),
            ("no simd", "c.wasm_simd = False"),
            ("no relaxed_simd", "c.wasm_relaxed_simd = False"),
            ("no parallel compilation", "c.parallel_compilation = False"),
            ("no tail_call", "c.wasm_tail_call = False"),
        ]
        for label, tweak in VARIANTS:
            probe = (
                "import sys, wasmtime\n"
                "_R = wasmtime.Config\n"
                "class C(_R):\n"
                "    def __init__(self, *a, **k):\n"
                "        super().__init__(*a, **k)\n"
                f"        c = self\n        {tweak or 'pass'}\n"
                "wasmtime.Config = C\n"
                "from intentumdiff.plugins.loader import load_plugin\n"
                "load_plugin(sys.argv[1], 10_000_000, trusted=True)\n"
                "print('loaded')\n"
            )
            rv = run([sys.executable, "-c", probe, str(target)])
            verdict = "SURVIVES" if rv.returncode == 0 else (
                "aborts" if rv.returncode in CRASH_CODES else f"error rc={rv.returncode}")
            print(f"  {label:26} {verdict}", flush=True)
            if rv.returncode not in CRASH_CODES and rv.returncode != 0:
                last = ((rv.stderr or "").strip().splitlines() or [""])[-1]
                print(f"    {last[:150]}", flush=True)
        # DO NOT return here. Finding one aborting component is not proof it is the only
        # problem - and returning early is exactly the mistake that cost a CI cycle: the
        # exclusion landed, arm64 still died, and we had never tested whether the REMAINING
        # components load together. Carry on with the survivors.
        print("\n  continuing with the survivors - one bad component may not be the only one",
              flush=True)

    section("do the loadable components survive TOGETHER?")

    # Only components that DID load alone. The staged set also contains non-parser components
    # (plugin_sdk, index_engine) which legitimately raise "exports neither parser, renderer,
    # enricher, nor diff-analyzer interface". Including them would end the search at the first
    # one and report a "budget" that is really just a bad input.
    bad = {n for n, _, _ in other} | set(crashed)
    good = [c for c in components if c.name not in bad]
    print(f"  bisecting over the {len(good)} loadable components", flush=True)

    n, last_ok = 1, 0
    while True:
        r = run([sys.executable, "-c", LOAD_MANY, *[str(c) for c in good[:n]]])
        died = r.returncode in CRASH_CODES
        if died:
            state = f"DIED rc={r.returncode} (fail-fast)"
        elif r.returncode != 0:
            # A Python-level error is information, not the crash we are hunting.
            state = f"error rc={r.returncode} (not a crash)"
        else:
            state = "ok"
        print(f"  {n:3} components in one process -> {state}", flush=True)

        if died:
            print(f"\n  budget: {last_ok} fit, {n} does not", flush=True)
            for line in (r.stderr or "").strip().splitlines()[-10:]:
                print(f"    err| {line}", flush=True)
            break
        last_ok = n
        if n >= len(good):
            print(f"  all {len(good)} load together here - no limit hit on this platform", flush=True)
            break
        n = min(n * 2, len(good))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
