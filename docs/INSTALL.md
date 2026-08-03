# Installing intentdiff

## From PyPI (the user path)

```bash
pip install intentdiff-python
```

Wheels are pre-built per platform and self-contained: the native engine and all parser
components are bundled. Python ≥ 3.12.

Optional extras: `intentdiff[serve]` (the local HTTP playground), `intentdiff[lsp]`,
`intentdiff[analytics]`.

## Verify

```bash
python -c "from intentdiff import SemanticDiffer; print('ok')"
intentdiff --help
```

## From source (development)

Building from source compiles the engine — see [BUILDING.md](BUILDING.md). The short form:

```bash
python scripts/provision_build_inputs.py --core-dir <intentdiff-core checkout> --wasm-dir <built components>
pip install cffi
pip install -e .[dev,serve]
```
