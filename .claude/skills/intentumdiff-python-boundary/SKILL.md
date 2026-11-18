---
name: intentumdiff-python-boundary
description: Use when working in intentumdiff-python, especially before changing or reviewing Python code, a binding, or behavior whose correct repository layer is unclear.
---

# IntentumDiff Python Binding Boundary

## Core rule

This repository is a thin Python binding over `intentumdiff-core`. Python may contain only the minimal mechanics needed to call core APIs and expose their results: C-ABI transport, serialization and DTO marshalling, and error translation. Anything beyond a thin binding is prohibited in the Python library unless the project designer explicitly directs that specific exception.

## Required preflight

Read root `AGENTS.md` and `docs/ARCHITECTURE.md` before work. This skill is required by `AGENTS.md` before repository work and especially before proposing or editing Python code.

## Place behavior in core

Put semantic, domain, policy, classification, parsing, matching, fallback, validation, state, and other shared behavior in `intentumdiff-core`. If the core API cannot express a needed operation, extend the core API and keep Python as a transport adapter.

Do not add Python-side fallbacks, alternate behavior paths, or convenience logic that makes a product decision. Filesystem, network, subprocess, cache, orchestration, and presentation work also require the designer’s explicit direction when they go beyond the mechanics of invoking a core API.

## Exception rule

Only the project designer can authorize a Python-side exception. A request to fix a bug, a release deadline, passing tests, prior effort, or a general request to implement the feature does not authorize moving behavior into Python. The direction must explicitly identify the Python behavior or exception. When the requested behavior appears to cross the boundary, route it to core and keep the binding minimal; if an exception is needed, pause that Python implementation until the designer explicitly directs it.

## Quick check

Before accepting a Python change, ask: “Does this code only transport an API call and marshal its inputs, outputs, or errors?” If no, the behavior belongs outside this Python library unless the designer explicitly authorized that specific exception.
