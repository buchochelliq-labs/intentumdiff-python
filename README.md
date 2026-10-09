# IntentumDiff

[![CI](https://github.com/buchochelliq-labs/intentumdiff-python/actions/workflows/ci.yml/badge.svg)](https://github.com/buchochelliq-labs/intentumdiff-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

**Semantic code review — see what a change means, not which lines moved.**

A textual diff shows that 40 lines changed. It cannot tell you whether that is a rename, a
function moved between files, a reformat, or a behavioural change hiding among them.
IntentumDiff parses both sides, matches the trees, and classifies every change by what it
does: **meaningful**, **refactoring**, **moved**, or **ignored style**.

## Install

```bash
pip install intentumdiff-python
```

Requires **Python 3.12+**. The wheel bundles the Rust engine and language parser
components; built-in parsing needs no separate component download.

**0.0.2 candidate platform limitation:** PowerShell is unavailable on Windows ARM64
because of the tracked compiler limitation in [#42](https://github.com/buchochelliq-labs/intentumdiff-python/issues/42).
Rust reports this parser as unavailable; the package does not substitute a Python engine.

> The distribution is `intentumdiff-python`; the import package is `intentumdiff`.

## Quick start

```python
from intentumdiff import SemanticDiffer

old = """def greet(name):
    return 'hi ' + name
"""
new = """def greet(name):
    if not name:
        return None
    return 'hi ' + name
"""

diff = SemanticDiffer().diff_strings(old, new, "example.py")
for change in diff.changes:
    print(change.change_type, change.description)
```

```text
ChangeType.ADDITION Insert -> if_statement('if_statement')
```

A line diff reports three added lines. IntentumDiff reports that a **guard clause** was
introduced — the thing a reviewer needs to know.

## Command line

```bash
intentumdiff --help          # or: python -m intentumdiff --help
intentumdiff file old.py new.py
intentumdiff git --help      # diff against a ref in a git repository
```

## See the 0.0.2 candidate CLI

This real installed-wheel recording changes the assigned value from `1` to `2`.
Rust reports one modification; the main diff panel and table use **rs-rich**.

[![Installed 0.0.2b1 candidate showing one integer-value modification](https://raw.githubusercontent.com/buchochelliq-labs/intentumdiff-docs/1c082d29f58a616136290db7003baec96aa3c200/docs/assets/cli/current/meaningful.png)](https://github.com/buchochelliq-labs/intentumdiff-docs/blob/1c082d29f58a616136290db7003baec96aa3c200/docs/assets/cli/current/meaningful.mp4)

[Watch the CLI examples and read their exact provenance](https://github.com/buchochelliq-labs/intentumdiff-docs/blob/1c082d29f58a616136290db7003baec96aa3c200/docs/cli-demos/index.md).
The recordings use the verified Python #98 Linux wheel and **rs-rich-record**. They are
pre-publication candidate evidence, not a claim that 0.0.2 is available on PyPI.
Standalone guardrail reports and errors still use the existing presentation paths.

## Documentation

**https://buchochelliq-labs.github.io/intentumdiff-docs/**

- [Getting started](https://buchochelliq-labs.github.io/intentumdiff-docs/getting-started/)
- [Library and CLI reference](https://buchochelliq-labs.github.io/intentumdiff-docs/python/)
- [How it works](https://buchochelliq-labs.github.io/intentumdiff-docs/concepts/)
- [Troubleshooting](https://buchochelliq-labs.github.io/intentumdiff-docs/troubleshooting/)

There is also a [VS Code extension](https://github.com/buchochelliq-labs/intentumdiff-vscode),
which uses this package as its engine.

## Privacy

Everything runs locally and there is no telemetry. The optional LLM explainer is off by
default and bring-your-own-key; when enabled for a cloud endpoint it sends a privacy-safe fact
sheet — counts, categories and flags — never your source, identifiers or literals. See the
[privacy policy](https://buchochelliq-labs.github.io/intentumdiff-docs/privacy/).

## Licence

MIT. Contributing and build-from-source instructions are in
[CONTRIBUTING.md](CONTRIBUTING.md).
