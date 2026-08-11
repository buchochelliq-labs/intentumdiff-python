"""Report what works on this platform, one capability at a time, and never crash doing it.

WHY THIS EXISTS
---------------
Widening CI to every platform we publish a wheel for (#9) turned windows-11-arm red in a way
that carried no information: the suite exited after ~12 seconds with no traceback, no
"collected N items", and - even with PYTHONFAULTHANDLER set - no fault handler output. That
combination rules out both an ordinary test failure and a hard native crash, and leaves
nothing to act on.

A test suite is a bad diagnostic instrument. It imports everything, collects everything, and
reports at the end, so any abrupt exit destroys the evidence for all of it. This walks the
same ground in order - interpreter, native runtime, package import, staged components, one
real diff, then collection - printing each result before attempting the next, so the last line
printed names the step that killed the process.

Every check is independently guarded. The script's own exit code is deliberately 0 unless
--strict is passed: it runs as a `continue-on-error` step whose value is the output, and a
non-zero exit there just adds a red X that explains nothing.
"""

from __future__ import annotations

import os
import pathlib
import platform
import subprocess
import sys
import traceback


# Run pytest's collection on a thread with an explicitly large stack. The MAIN thread's stack
# size is fixed in the executable's PE header and cannot be changed at runtime on Windows; a
# thread created afterwards gets whatever threading.stack_size asks for. If a stack overrun is
# what kills collection, this is the variant that survives - and the fix.
_BIG_STACK_RUNNER = """
import sys, threading
threading.stack_size(64 * 1024 * 1024)
rc = {}
def run():
    import pytest
    rc['code'] = pytest.main(['tests/unit', '--collect-only', '-q'])
t = threading.Thread(target=run)
t.start()
t.join()
sys.exit(int(rc.get('code', 99)))
"""


def section(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def attempt(label: str, fn) -> bool:
    """Run fn, print what happened, and keep going regardless."""
    try:
        result = fn()
    except BaseException:  # noqa: BLE001 - SystemExit included on purpose; see below
        print(f"  {label}: FAILED", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        return False
    print(f"  {label}: ok{'' if result is None else f' - {result}'}", flush=True)
    return True


def main() -> int:
    section("interpreter")
    print(f"  {sys.version}", flush=True)
    print(f"  machine={platform.machine()}  platform={platform.platform()}", flush=True)
    print(f"  maxsize={sys.maxsize}  executable={sys.executable}", flush=True)

    section("native runtime")

    def _wasmtime():
        import wasmtime

        return f"{getattr(wasmtime, '__version__', '?')} at {wasmtime.__file__}"

    attempt("import wasmtime", _wasmtime)

    def _engine():
        # Instantiating an Engine is where a runtime unsupported on this architecture would
        # first actually execute native code, rather than merely load a shared library.
        import wasmtime

        wasmtime.Engine()
        return "Engine() constructed"

    attempt("wasmtime.Engine()", _engine)

    section("package")

    def _import():
        import intentumdiff

        return f"{intentumdiff.__version__} at {intentumdiff.__file__}"

    imported = attempt("import intentumdiff", _import)

    if imported:

        def _components():
            import pathlib

            import intentumdiff

            d = pathlib.Path(intentumdiff.__file__).parent / "wasm"
            n = len(sorted(d.glob("*.wasm"))) if d.is_dir() else 0
            return f"{n} components in {d}"

        attempt("staged components", _components)

        def _core():
            from intentumdiff.plugins import loader

            return f"loader module at {loader.__file__}"

        attempt("import the plugin loader", _core)

        def _diff():
            import tempfile
            from pathlib import Path

            from intentumdiff import FileSource, SemanticDiffer

            with tempfile.TemporaryDirectory() as tmp:
                old = Path(tmp) / "old.py"
                new = Path(tmp) / "new.py"
                old.write_text("def f():\n    return 1\n", encoding="utf-8")
                new.write_text("def f():\n    return 2\n", encoding="utf-8")
                d = SemanticDiffer().diff(FileSource(old, new))
            return f"{len(d.changes)} change(s), language={getattr(d, 'language', '?')}"

        attempt("one real diff", _diff)

    section("pytest collection")
    # Each variant runs in a CHILD process on purpose. If collection kills the interpreter,
    # that must not take this script's remaining output with it - the point is to still
    # report, and to keep going so one crash does not hide the next answer.
    #
    # windows-11-arm returns 3221226505 = 0xC0000409 = STATUS_STACK_BUFFER_OVERRUN, a Windows
    # fail-fast, with no output at all. The product itself is fine there - the real diff above
    # succeeds - so this is about how pytest COLLECTS, not what it collects. These variants
    # separate the candidate causes in one run rather than one per CI cycle.
    # ANSWERED already on windows-11-arm, so those probes are gone:
    #   baseline          CRASHED 0xC0000409   (STATUS_STACK_BUFFER_OVERRUN)
    #   --assert=plain    CRASHED              -> not pytest's AST rewriting
    #   64MB stack thread CRASHED              -> not stack size
    #   one file          ok, 69 collected     -> collection itself is fine
    #
    # One module collects; the whole tree does not. That leaves a specific module, or
    # something cumulative across modules. Both are bisectable, so bisect.
    files = sorted(str(p).replace("\\", "/") for p in pathlib.Path("tests/unit").glob("test_*.py"))
    print(f"  {len(files)} test modules to bisect", flush=True)

    def collect(paths: list[str]) -> int:
        return subprocess.run(
            [sys.executable, "-m", "pytest", *paths, "--collect-only", "-q", "-p", "no:cacheprovider"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).returncode

    CRASH = 3221226505  # 0xC0000409

    # Ask the cheap question first. On a healthy platform this is one subprocess and the
    # answer is "nothing to investigate" - sweeping ~200 modules individually there would
    # cost minutes to confirm what one call already told us.
    section("does the full tree crash on this platform?")
    full_rc = collect(files)
    if full_rc != CRASH:
        print(f"  no - exit {full_rc}. Nothing to bisect here.", flush=True)
    else:
        print(f"  yes - {full_rc} (0x{full_rc & 0xFFFFFFFF:08x}). Bisecting.", flush=True)

        section("phase 1: how many modules together does it take?")
        # Smallest crashing prefix of the sorted list. Binary search, ~8 trials for 200
        # modules, each in a fresh child so a crash costs a process rather than the bisect.
        lo, hi = 1, len(files)
        while lo < hi:
            mid = (lo + hi) // 2
            rc = collect(files[:mid])
            print(f"  first {mid:3} modules -> {'CRASH' if rc == CRASH else f'ok({rc})'}", flush=True)
            if rc == CRASH:
                hi = mid
            else:
                lo = mid + 1
        print(f"\n  smallest crashing prefix: {lo} of {len(files)} modules", flush=True)
        print(f"  the module that tips it over: {files[lo - 1]}", flush=True)

        section("phase 2: is that module poisonous, or just the last straw?")
        alone = collect([files[lo - 1]])
        if alone == CRASH:
            print(f"  it crashes ALONE too -> that module is the problem, not the volume", flush=True)
        else:
            print(f"  it collects fine alone (exit {alone}) -> the trigger is CUMULATIVE.", flush=True)
            print("  Something is retained across module imports and exhausts a limit:", flush=True)
            print("  handles, memory, or wasmtime instances. The prefix size is the budget.", flush=True)

    section("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
