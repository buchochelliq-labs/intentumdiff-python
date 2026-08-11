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
import platform
import subprocess
import sys
import traceback


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
    # In a CHILD process on purpose. If collection kills the interpreter, that must not take
    # this script's remaining output with it - the whole point is to still be able to report.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"  exit code: {proc.returncode}", flush=True)
    tail = (proc.stdout or "").strip().splitlines()[-12:]
    for line in tail:
        print(f"  out| {line}", flush=True)
    for line in (proc.stderr or "").strip().splitlines()[-25:]:
        print(f"  err| {line}", flush=True)
    if not tail and not (proc.stderr or "").strip():
        print("  NO OUTPUT AT ALL - the collector died without writing anything", flush=True)

    section("done")
    return 1 if "--strict" in sys.argv and proc.returncode != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
