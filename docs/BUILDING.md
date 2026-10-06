# Building intentumdiff (Python) from source

CI toolchains: **Python 3.12**, **Rust 1.95.0** (the wheel build compiles the engine).

## 1. Provision the build inputs

The engine and the parser/renderer components come from separate repositories.
The split Python repository does not require the retired monorepo. To use local
build inputs:

```bash
python scripts/provision_build_inputs.py \
    --core-dir /path/to/intentumdiff-core \
    --wasm-dir /path/to/built/components
```

This stages `build/intentumdiff-core/` (pyproject's `[tool.maturin] manifest-path` points into
it) and `src/intentumdiff/wasm/*.wasm`. Alongside the parsers, include the complete
four-renderer set in the component directory: `terminal_renderer.wasm`,
`patch_renderer.wasm`, `html_renderer.wasm` and `llm_renderer.wasm`. All four are
registered built-ins and are required for complete component discovery, including
when `INTENTUMDIFF_REQUIRE_ALL_COMPONENTS=1`.
By default, omitting `--core-dir` fetches the immutable `CORE_REF` pinned in the
provisioning script. A local checkout (`--core-dir` or `INTENTUMDIFF_CORE_DIR`) or
`INTENTUMDIFF_CORE_REF` is an explicit override of that pin.

For the artifact-backed CI path, configure Git authentication for the sibling
repositories and provide `GH_TOKEN` or `GITHUB_TOKEN` with read access to their
Actions artifacts, then run:

```bash
python -m pip install "pyyaml>=6.0"
python scripts/provision_build_inputs.py --from-parser-artifacts
```

Provisioning without either a component directory or `--from-parser-artifacts`
skips component staging. That is not a complete release-wheel build.

For local components, `--wasm-dir` copies `wasm_provenance.json` when supplied.
If your component directory has no manifest, generate one from the staged files:

```bash
python scripts/wasm_provenance.py generate
python scripts/wasm_provenance.py verify
```

Artifact-backed provisioning generates this manifest automatically. The wheel must
include it; installed-wheel smoke verifies its complete component set and hashes.

## 2. Build

```bash
python -m pip install cffi
python -m pip install -e ".[dev,serve]"
```

Install `cffi` first: maturin needs it importable by the target interpreter.
To build a wheel instead of an editable installation:

```bash
python -m pip install cffi "maturin>=1.14,<2"
maturin build --release -b cffi --out dist
```

## 3. Test

```bash
python -m pytest tests/unit -q
```

Runtime semantic tests use provisioned components and the public API through the
ctypes path. They do not depend on monorepo source files. Platform skips must be
classified in `tests/unit/skip_reasons_baseline.json`; PowerShell scenarios on Windows
ARM64 skip only when Rust explicitly reports the parser unavailable.

Verify the live backend if in doubt:
`python -c "import intentumdiff.rust_core as r; print(type(r._load_backend()).__name__)"`
must print `_CtypesBackend`.

Gotcha: a leftover pyo3-era `src/intentumdiff/*.pyd` or a standalone `intentumdiff_rust_core`
pip install shadows the fresh cdylib — remove/uninstall them after rebuilds.


### Reviewed parser registry for 0.0.2

Wheel provisioning pins registry commit `839b616c2f82b344028dfe75a9dd7f736452169d`
(registry PR #7). This reconciles the post-rebrand component set with the reviewed
JS/TS incomplete-source fix; reading the registry default branch had silently
omitted that fix. An incomplete `function f(` → `function g(` edit must produce
Rust-owned source fallback with an explicit parse-error indication, never empty
style-only success. Installed-wheel smoke checks cover Python, JavaScript and
TypeScript with exact changed identifier ranges. Parser bytes remain checksum
verified and the wheel includes its complete Wasm provenance.


### Rust-owned terminal presentation

Default diff output and `--format terminal --output FILE` use the same Rust
`rs-rich` renderer as the native CLI. Python passes the authoritative diff DTO,
terminal width and colour preference through the existing C ABI, then writes the
returned text without interpreting markup. `NO_COLOR` and redirected output
remain colour-free. Narrow terminals use wrapping and stacked panels so protected
values and identifiers remain visible. Source fallback explicitly says semantic
equivalence is unknown. JSON, patch, HTML, LLM and SARIF do not use this renderer.

This is bounded adoption for diff summaries, change tables and their guardrail
rows. Command help, banners and other CLI screens still use the existing host
presentation; their migration is separate from this change.
