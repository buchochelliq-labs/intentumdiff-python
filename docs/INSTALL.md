# Installing intentumdiff

## From PyPI (the user path)

```bash
pip install intentumdiff-python
```

Wheels are pre-built per platform and self-contained: the native engine and all parser
components are bundled. Python ≥ 3.12.

Optional extras: `intentumdiff[serve]` (the local HTTP playground), `intentumdiff[lsp]`,
`intentumdiff[analytics]`.

## Verify

```bash
python -c "from intentumdiff import SemanticDiffer; print('ok')"
intentumdiff --help
```

## From source (development)

Building from source compiles the engine — see [BUILDING.md](BUILDING.md). The short form:

```bash
python scripts/provision_build_inputs.py --core-dir <intentumdiff-core checkout> --wasm-dir <built components>
pip install cffi
pip install -e .[dev,serve]
```
