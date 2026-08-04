"""Trivial-body → real-body matrix (issue #42): the #41 class, every language.

Issue #41 was a false style-only for python ``pass`` → ``print(...)``. Every
language has an equivalent trivial/empty body idiom; each parser must prove the
transition to a real body SURFACES (never zero changes, never style-only).
Table-driven and oracle-free — the assertion is existence, not shape.
"""

from __future__ import annotations

import pytest

from intentumdiff import SemanticDiffer

# (language, filename, trivial-body source, real-body source)
CASES = [
    ("python", "m.py", "def f():\n    pass\n", "def f():\n    print('x')\n"),
    ("python", "m.py", "def f():\n    ...\n", "def f():\n    return compute()\n"),
    ("javascript", "m.js", "function f() {}\n", "function f() { console.log('x'); }\n"),
    ("typescript", "m.ts", "function f(): void {}\n", "function f(): void { console.log('x'); }\n"),
    ("go", "m.go", "package m\nfunc f() {}\n", "package m\nfunc f() {\n\tprintln(1)\n}\n"),
    ("rust", "m.rs", "fn f() {}\n", "fn f() {\n    println!(\"x\");\n}\n"),
    ("rust", "m.rs", "fn f() {\n    todo!()\n}\n", "fn f() {\n    compute();\n}\n"),
    ("java", "M.java", "class M { void f() {} }\n", "class M { void f() { run(); } }\n"),
    ("csharp", "M.cs", "class M { void F() {} }\n", "class M { void F() { Run(); } }\n"),
    ("cpp", "m.cpp", "void f() {}\n", "void f() { run(); }\n"),
    ("ruby", "m.rb", "def f\nend\n", "def f\n  puts 'x'\nend\n"),
    ("perl", "m.pl", "sub f {}\n", "sub f { print \"x\"; }\n"),
    ("delphi", "m.pas", "procedure F;\nbegin\nend;\n", "procedure F;\nbegin\n  WriteLn('x');\nend;\n"),
    ("bash", "m.sh", "f() {\n  :\n}\n", "f() {\n  echo x\n}\n"),
    ("elixir", "m.ex", "def f do\nend\n", "def f do\n  IO.puts(\"x\")\nend\n"),
    ("dart", "m.dart", "void f() {}\n", "void f() { print('x'); }\n"),
    ("lua", "m.lua", "function f() end\n", "function f() print('x') end\n"),
    ("kotlin", "m.kt", "fun f() {}\n", "fun f() { println(\"x\") }\n"),
    ("swift", "m.swift", "func f() {}\n", "func f() { print(\"x\") }\n"),
    ("php", "m.php", "<?php\nfunction f() {}\n", "<?php\nfunction f() { run(); }\n"),
    ("scala", "m.scala", "object M { def f(): Unit = {} }\n", "object M { def f(): Unit = { run() } }\n"),
    ("markdown", "m.md", "# Title\n", "# Title\n\nBody content arrives.\n"),
]

_IDS = [
    f"{(case.values[0] if hasattr(case, 'values') else case[0])}-{i}"
    for i, case in enumerate(CASES)
]


@pytest.mark.parametrize(("language", "filename", "trivial", "real"), CASES, ids=_IDS)
def test_trivial_body_to_real_body_is_meaningful(
    language: str, filename: str, trivial: str, real: str
) -> None:
    diff = SemanticDiffer().diff_strings(trivial, real, filename=filename, language_hint=language)
    assert diff.changes, f"{language}: trivial→real body produced ZERO changes (the #41 disease)"
    assert not diff.is_style_only, f"{language}: trivial→real body classified STYLE-ONLY (the #41 disease)"
