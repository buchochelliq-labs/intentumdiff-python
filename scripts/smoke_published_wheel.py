"""Install the PUBLISHED wheel into a clean venv and use it as a user would.

# Why this exists

IntentumDiff 0.0.1 shipped broken and had to be pulled. Every invocation printed ~69
"Failed to catalog parser plugin" errors, because the distribution name
(`intentumdiff-python`) was missing from the package's own first-party trust list, so all
69 built-in parsers were refused as untrusted third-party code.

**No test in the repo could have caught it.** In a source checkout the distribution resolves
differently, so the trust check passed. The bug only exists in an installed wheel — and
nothing in CI ever installed one and ran it.

That is the gap this closes: CI proves the code builds; this proves the ARTEFACT works.

# What it checks

Everything a new user does in their first five minutes, in order:

1. `pip install` from the index into an empty venv
2. `import intentumdiff`
3. the console script runs
4. `python -m intentumdiff` works
5. a real diff produces a real result
6. **stderr is CLEAN** — the check that would have caught 0.0.1
7. every URL in the error catalogue actually resolves

Usage:
    python scripts/smoke_published_wheel.py                    # from PyPI
    python scripts/smoke_published_wheel.py --wheel dist/x.whl # a local build, pre-publish
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# A diff with an obvious right answer: a guard clause is added, so exactly one change.
OLD_SRC = "def greet(name):\n    return 'hi ' + name\n"
NEW_SRC = "def greet(name):\n    if not name:\n        return None\n    return 'hi ' + name\n"

USE_SCRIPT = """
from intentumdiff import SemanticDiffer
old = {old!r}
new = {new!r}
diff = SemanticDiffer().diff_strings(old, new, "example.py")
print("CHANGES", len(diff.changes))
"""


class Smoke:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.py = (
            root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
            / ("python.exe" if sys.platform == "win32" else "python")
        )
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            if detail:
                print(f"        {detail.strip()[:400]}")
            self.failures.append(name)

    def run(self, *args: str, **kw) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.py), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=self.root, **kw
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wheel", help="local wheel or sdist; defaults to installing from PyPI")
    ap.add_argument("--package", default="intentumdiff-python")
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="intentumdiff-smoke-"))
    print(f"  clean environment: {root}\n")
    try:
        venv.create(root / ".venv", with_pip=True)
        s = Smoke(root)

        # 1. Install exactly as a user would.
        target = args.wheel or args.package
        r = s.run("-m", "pip", "install", "--quiet", target)
        s.check(f"pip install {target}", r.returncode == 0, r.stderr)
        if r.returncode != 0:
            return report(s)

        # 2. Import.
        r = s.run("-c", "import intentumdiff; print(intentumdiff.__version__)")
        s.check("import intentumdiff", r.returncode == 0, r.stderr)
        version = r.stdout.strip()

        # 3. Console script — the documented entry point.
        exe = s.py.parent / ("intentumdiff.exe" if sys.platform == "win32" else "intentumdiff")
        r2 = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        s.check("console script `intentumdiff --version`", r2.returncode == 0, r2.stderr)

        # 4. `python -m` — READMEs reach for this constantly, so it must work.
        r = s.run("-m", "intentumdiff", "--version")
        s.check("python -m intentumdiff", r.returncode == 0, r.stderr)

        # 5. A real diff with a known-correct answer.
        script = root / "use.py"
        script.write_text(USE_SCRIPT.format(old=OLD_SRC, new=NEW_SRC), encoding="utf-8")
        r = s.run(str(script))
        s.check("SemanticDiffer produces a diff", "CHANGES" in r.stdout, r.stderr)

        # 6. THE ONE THAT MATTERS. 0.0.1 emitted ~69 plugin-catalogue errors on every
        #    invocation while still returning a result, so exit code alone said "fine".
        noise = [
            ln for ln in r.stderr.splitlines()
            if ln.strip() and "Failed to catalog" in ln
        ]
        s.check(
            "no plugin-catalogue errors on stderr",
            not noise,
            f"{len(noise)} error line(s), first: {noise[0] if noise else ''}",
        )

        # 7. Every URL the package tells users to visit must resolve. 0.0.1 pointed at
        #    docs.intentumdiff.dev, which does not exist.
        import re
        import urllib.error
        import urllib.request

        urls = sorted(set(re.findall(r"https?://[^\s'\"<>)\]]+", r.stderr)))
        dead = []
        for u in urls:
            try:
                req = urllib.request.Request(u, method="HEAD",
                                             headers={"User-Agent": "intentumdiff-smoke"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status >= 400:
                        dead.append(f"{u} -> {resp.status}")
            except urllib.error.HTTPError as e:
                dead.append(f"{u} -> {e.code}")
            except Exception as e:  # DNS failure counts: the domain does not exist
                dead.append(f"{u} -> {type(e).__name__}")
        s.check(
            f"all {len(urls)} URL(s) in output resolve",
            not dead,
            "; ".join(dead[:3]),
        )

        print(f"\n  version under test: {version or '(unknown)'}")
        return report(s)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def report(s: Smoke) -> int:
    if s.failures:
        print(f"\n  SMOKE FAILED: {len(s.failures)} check(s) — {', '.join(s.failures)}")
        return 1
    print("\n  SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
