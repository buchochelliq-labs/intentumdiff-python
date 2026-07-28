# Building intentdiff (Python) from source

Toolchains: **Python 3.12**, **Rust 1.93.0** (the wheel build compiles the engine).

## 1. Provision the build inputs

The engine and the parser components are separate repos' artifacts:

```bash
python scripts/provision_build_inputs.py \
    --core-dir /path/to/intentdiff-core \      # or omit to clone the repo
    --wasm-dir /path/to/built/components       # parser .wasm set to bundle
```

This stages `build/intentdiff-core/` (pyproject's `[tool.maturin] manifest-path` points into
it) and `src/intentdiff/wasm/*.wasm`.

## 2. Build

```bash
pip install cffi          # REQUIRED before the build: maturin's cffi-bindings mode
                          # needs the cffi module importable by the target interpreter
pip install -e .[dev,serve]          # editable dev install (drops the cdylib in-tree)
# or a wheel:
maturin build --release -b cffi --out dist
```

## 3. Test

```bash
python -m pytest tests/unit -q
```

The suite runs entirely over the ctypes path. Verify the live backend if in doubt:
`python -c "import intentdiff.rust_core as r; print(type(r._load_backend()).__name__)"`
must print `_CtypesBackend`.

Gotcha: a leftover pyo3-era `src/intentdiff/*.pyd` or a standalone `intentdiff_rust_core`
pip install shadows the fresh cdylib — remove/uninstall them after rebuilds.
