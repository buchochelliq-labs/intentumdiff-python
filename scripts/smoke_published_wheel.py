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
import json
import re
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


# Executed by the clean installed interpreter, never against source-tree assets.
PROVENANCE_SCRIPT = """
import hashlib
import json
from importlib.resources import files

root = files('intentumdiff').joinpath('wasm')
manifest = json.loads(root.joinpath('wasm_provenance.json').read_text(encoding='utf-8'))
if manifest.get('schema_version') != 1:
    raise ValueError('unsupported Wasm provenance schema')
artifacts = manifest['artifacts']
actual = {p.name: p for p in root.iterdir() if p.name.endswith('.wasm')}
if not actual or set(actual) != set(artifacts):
    raise ValueError('installed Wasm set differs from provenance manifest')
if manifest.get('artifact_count') != len(actual):
    raise ValueError('Wasm provenance artifact count mismatch')
for name, path in actual.items():
    data = path.read_bytes()
    expected = artifacts[name]
    if len(data) != expected['size_bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise ValueError('installed Wasm provenance mismatch: ' + name)
print('VERIFIED', len(actual), 'installed Wasm components')
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



RECOGNIZED_TEXT_REVIEW_SCRIPT = r'''
import subprocess
from pathlib import Path
from intentumdiff import SemanticDiffer

repo = Path("recognized-text-review").resolve()
repo.mkdir()
cases = {
    "code.sh": "#!/bin/bash\necho One\n",
    "index.html": "<!DOCTYPE html><html><title>One</title></html>",
    "data.xml": '<?xml version="1.0"?><root>One</root>',
    "Component.svelte": "<script>let name = 'One';</script><h1>{name}</h1>",
    "module.wat": '(module (func (export "One") (result i32) i32.const 1))',
    "module.wast": '(module (func (export "One") (result i32) i32.const 1))',
    "code.ps": "%!PS\n(One) show\nshowpage\n",
}
def git(*args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
git("init")
for name, old in cases.items():
    (repo / name).write_text(old, encoding="utf-8")
git("add", ".")
git("-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid", "commit", "-m", "baseline")
for name, old in cases.items():
    (repo / name).write_text(old.replace("One", "Two"), encoding="utf-8")
diffs = SemanticDiffer().diff_commit(str(repo), "HEAD", "")
assert {Path(d.new_filename).name for d in diffs} == set(cases), [(d.new_filename, d.language) for d in diffs]
assert all(d.changes and not d.is_style_only and d.language != "binary" for d in diffs)
print("All seven recognized text formats retain their actual Git changes")
from intentumdiff.plugins.exceptions import PluginOutputError
for tag in ("script", "style"):
    for text in ("é", "漢", "😀", "\né", "é\n"):
        try:
            diff = SemanticDiffer().diff_strings(f"<{tag}>{text}", f"<{tag}>{text}x", "App.svelte")
        except PluginOutputError as error:
            assert "Unclosed script or style block" in str(error), str(error)
        else:
            assert diff.changes and diff.parse_errors and not diff.is_style_only, diff
print("Unfinished Unicode Svelte blocks report explicit errors, never empty success")
'''

def _repo_readme() -> str | None:
    """The README a user reads. Checked in preference order, nearest first."""
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent / "README.md", here.parent / "README.md"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def _first_python_block(markdown: str) -> str | None:
    """The first fenced ``python`` block — the headline example, the one people copy."""
    fence = "`" * 3
    pattern = fence + r"python\n(.*?)" + fence
    match = re.search(pattern, markdown, re.DOTALL)
    return match.group(1) if match else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wheel", help="local wheel or sdist; defaults to installing from PyPI")
    ap.add_argument("--package", default="intentumdiff-python")
    args = ap.parse_args()

    # Resolve a local artefact to an ABSOLUTE path before going anywhere near the temp
    # directory. Every command below runs with the clean environment as its working
    # directory, so a relative `dist/foo.whl` resolves against THAT, and pip reports:
    #
    #   WARNING: Requirement 'dist/foo.whl' looks like a filename, but the file does not exist
    #   OSError: [Errno 2] No such file or directory: '/private/var/.../dist/foo.whl'
    #
    # which reads like a broken wheel and is actually a broken path. Found the first time CI
    # smoked a wheel it had just built - the first caller ever to pass a relative one.
    if args.wheel:
        wheel_path = Path(args.wheel).expanduser().resolve()
        if not wheel_path.is_file():
            print(f"  no such artefact: {args.wheel!r} (resolved to {wheel_path})")
            return 1
        args.wheel = str(wheel_path)

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

        r = s.run("-c", PROVENANCE_SCRIPT)
        s.check("installed Wasm provenance matches every bundled component",
                r.returncode == 0, r.stderr)

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

        # Incomplete identifiers are real edits, never formatting-only success.
        # This catches stale parser pins in the installed package, not just source tests.
        for extension, old, new, offset in (
            ("py", "def f(", "def g(", 4),
            ("js", "function f(", "function g(", 9),
            ("ts", "function f(", "function g(", 9),
        ):
            before = root / f"incomplete-before.{extension}"
            after = root / f"incomplete-after.{extension}"
            before.write_text(old, encoding="utf-8")
            after.write_text(new, encoding="utf-8")
            result = subprocess.run(
                [str(exe), "diff", "--json", str(before), str(after)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=root, timeout=120,
            )
            try:
                diff = json.loads(result.stdout)
                changes = diff["changes"]
                metadata = diff["metadata"]
                valid = (
                    result.returncode == 0 and diff["is_fallback"] is True
                    and diff["is_style_only"] is False and bool(diff["parse_errors"])
                    and metadata["engine_owner"] == "rust"
                    and metadata["semantic_contract"] == "rust_source_fallback_v1"
                    and len(changes) == 1
                    and changes[0]["old_node"]["label"] == "f"
                    and changes[0]["new_node"]["label"] == "g"
                    and metadata["source_ranges"] == {
                        "old_start_byte": offset, "old_end_byte": offset + 1,
                        "new_start_byte": offset, "new_end_byte": offset + 1,
                    }
                )
            except (ValueError, KeyError, TypeError):
                valid = False
            s.check(f"installed wheel preserves incomplete {extension} edit", valid,
                    (result.stderr or result.stdout)[:1000])

        r = s.run("-c", RECOGNIZED_TEXT_REVIEW_SCRIPT, timeout=180)
        s.check("installed wheel Git review retains seven recognized text formats",
                r.returncode == 0, (r.stderr or r.stdout)[:2000])

        # 5b. Advertised Wasm renderer formats must work from the INSTALLED wheel.
        # These all shipped broken in 0.0.2b1 because the CLI looked under
        # intentumdiff/cli/wasm instead of the package's real component directory.
        old_file = root / "old.py"
        new_file = root / "new.py"
        old_file.write_text(OLD_SRC, encoding="utf-8")
        new_file.write_text(NEW_SRC, encoding="utf-8")
        for fmt in ("patch", "html", "llm"):
            rendered = subprocess.run(
                [
                    str(exe),
                    "file",
                    str(old_file),
                    str(new_file),
                    "--format",
                    fmt,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=root,
            )
            s.check(
                f"installed wheel renders --format {fmt}",
                rendered.returncode == 0 and bool(rendered.stdout.strip()),
                (rendered.stderr or rendered.stdout).strip()[:400],
            )

        # 5b. THE README'S OWN EXAMPLE, extracted and executed verbatim.
        #
        #     Check 5 above runs OLD_SRC/NEW_SRC — this file's PRIVATE copy of the example.
        #     That proves the library works; it proves nothing about what we published. The
        #     0.0.1 README shipped a headline example that raised NameError, and a gate that
        #     asserts on its own copy would have passed that release too.
        #
        #     It nearly happened again: on the 0.0.2 release candidate the example's triple
        #     quotes had collapsed to single quotes, so it died with SyntaxError at PARSE
        #     time — before importing anything — while every other check here stayed green.
        #
        #     So extract the first ```python fence from the README a user actually reads and
        #     run it against the INSTALLED wheel.
        readme = _repo_readme()
        if readme is None:
            s.check("README example runs verbatim", False, "README.md not found")
        else:
            block = _first_python_block(readme)
            if block is None:
                s.check("README example runs verbatim", False, "no ```python block in README.md")
            else:
                example = root / "readme_example.py"
                example.write_text(block, encoding="utf-8")
                r_readme = s.run(str(example))
                s.check(
                    "README example runs verbatim",
                    r_readme.returncode == 0,
                    (r_readme.stderr or r_readme.stdout).strip()[:400],
                )

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
