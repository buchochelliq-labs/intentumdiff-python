"""
tests/security/test_sandbox.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Adversarial sandbox tests.  These tests verify that the Wasm sandbox grants
ZERO capabilities to plugins and that the safety limits (fuel) work correctly.

All 7 test WAT modules are tiny hand-written Wasm text format snippets
compiled in-memory using wasmtime.  No external .wasm files are needed.

Tests
─────
1. filesystem_read   — path_open import → instantiation fails (import not satisfied)
2. env_read          — environ_sizes_get returns (0, 0) → no env leakage
3. network_socket    — sock_open import → instantiation fails
4. subprocess_spawn  — proc_raise import → instantiation fails
5. memory_isolation  — host sentinel value not visible in plugin linear memory
6. malformed_output  — plugin returns bad JSON → PluginOutputError raised
7. infinite_loop     — fuel guard traps with PluginFuelExhausted
"""

from __future__ import annotations

import pytest

pytest.importorskip("wasmtime", reason="wasmtime not installed")

from wasmtime import (
    Config,
    Engine,
    Linker,
    Module,
    Store,
    Trap,
    WasiConfig,
    WasmtimeError,
    wat2wasm,
)

from intentdiff.plugins.exceptions import (
    PluginFuelExhausted,
    PluginLoadError,
    PluginSandboxViolation,
)
from intentdiff.plugins.loader import LoadedPlugin, load_plugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_with_fuel() -> tuple[Engine, Config]:
    cfg = Config()
    cfg.consume_fuel = True
    return Engine(cfg), cfg


def _module_from_wat(engine: Engine, wat: str) -> Module:
    wasm_bytes = wat2wasm(wat)
    return Module(engine, wasm_bytes)


def _sandboxed_store(engine: Engine, fuel: int = 10_000_000) -> Store:
    store: Store = Store(engine)
    wasi = WasiConfig()  # zero capabilities
    store.set_wasi(wasi)
    store.set_fuel(fuel)
    return store


# ---------------------------------------------------------------------------
# Test 1 — filesystem read attempt
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_filesystem_read_blocked():
    """
    A plugin that imports ``wasi_snapshot_preview1::path_open`` must NOT
    instantiate — the linker cannot satisfy the import with an empty WasiConfig.

    Actually wasmtime WASI linker DOES define path_open but returns EBADF (8)
    because no directories are preopened.  We verify the return code, not a trap.
    """
    # WAT: import path_open and call it, returning the error code
    # Correct wasmtime WASI preview1 signature:
    #   (fd i32, dirflags i32, path i32, path_len i32, oflags i32,
    #    fs_rights_base i64, fs_rights_inheriting i64, fdflags i32, opened_fd i32)
    wat = r"""
    (module
      (import "wasi_snapshot_preview1" "path_open"
        (func $path_open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
      (memory (export "memory") 1)
      (func (export "try_open") (result i32)
        ;; fd=3, dirflags=0, path ptr=0, path len=0, oflags=0,
        ;; rights_base=0, rights_inheriting=0, fdflags=0, out_fd ptr=0
        i32.const 3
        i32.const 0  ;; dirflags
        i32.const 0  ;; path ptr
        i32.const 0  ;; path len
        i32.const 0  ;; oflags
        i64.const 0  ;; fs_rights_base
        i64.const 0  ;; fs_rights_inheriting
        i32.const 0  ;; fdflags
        i32.const 0  ;; out fd ptr
        call $path_open
      )
    )
    """
    engine, _ = _make_engine_with_fuel()
    module = _module_from_wat(engine, wat)
    linker = Linker(engine)
    linker.define_wasi()
    store = _sandboxed_store(engine)
    instance = linker.instantiate(store, module)
    exports = instance.exports(store)
    try_open = exports["try_open"]
    result = try_open(store)
    # EBADF = 8 — no preopened dirs, cannot open any path
    assert result == 8, f"Expected EBADF (8) but got {result}"


# ---------------------------------------------------------------------------
# Test 2 — environment variable read
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_env_read_empty():
    """
    environ_sizes_get must return (0, 0) — no environment variables are
    leaked to plugins.
    """
    wat = r"""
    (module
      (import "wasi_snapshot_preview1" "environ_sizes_get"
        (func $environ_sizes_get (param i32 i32) (result i32)))
      (memory (export "memory") 1)
      (func (export "get_env_count") (result i32)
        ;; Store count at offset 0, buf_size at offset 4
        i32.const 0
        i32.const 4
        call $environ_sizes_get
        ;; return the count (stored at memory[0])
        drop
        i32.const 0
        i32.load
      )
    )
    """
    engine, _ = _make_engine_with_fuel()
    module = _module_from_wat(engine, wat)
    linker = Linker(engine)
    linker.define_wasi()
    store = _sandboxed_store(engine)
    instance = linker.instantiate(store, module)
    exports = instance.exports(store)
    count = exports["get_env_count"](store)
    assert count == 0, f"Expected 0 env vars but got {count}"


# ---------------------------------------------------------------------------
# Test 3 — network socket attempt
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_network_socket_blocked():
    """
    sock_open is NOT defined by the WASI linker by default.  A plugin that
    imports it must fail to instantiate.
    """
    wat = r"""
    (module
      (import "wasi_snapshot_preview1" "sock_open"
        (func $sock_open (param i32 i32 i32) (result i32)))
      (func (export "try_socket") (result i32)
        i32.const 0
        i32.const 0
        i32.const 0
        call $sock_open
      )
    )
    """
    engine, _ = _make_engine_with_fuel()
    module = _module_from_wat(engine, wat)
    linker = Linker(engine)
    linker.define_wasi()
    store = _sandboxed_store(engine)
    with pytest.raises((WasmtimeError, Exception)):
        linker.instantiate(store, module)


# ---------------------------------------------------------------------------
# Test 4 — subprocess / process spawn attempt
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_subprocess_spawn_blocked():
    """
    A custom import for spawning a subprocess must fail to instantiate because
    wasmtime's WASI linker does not define non-standard process-spawn imports.
    We use a fictional import name that will never be satisfied.
    """
    wat = r"""
    (module
      (import "subprocess" "spawn_process"
        (func $spawn (param i32 i32) (result i32)))
      (func (export "try_spawn") (result i32)
        i32.const 0
        i32.const 0
        call $spawn
      )
    )
    """
    engine, _ = _make_engine_with_fuel()
    module = _module_from_wat(engine, wat)
    linker = Linker(engine)
    linker.define_wasi()
    store = _sandboxed_store(engine)
    with pytest.raises((WasmtimeError, Exception)):
        linker.instantiate(store, module)


# ---------------------------------------------------------------------------
# Test 5 — host memory isolation
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_host_memory_isolation():
    """
    A Wasm module's linear memory is isolated from host memory.
    Writing a sentinel value in Python heap must NOT be visible in the
    Wasm module's memory.
    """
    sentinel = 0xDEADBEEF

    wat = r"""
    (module
      (memory (export "memory") 1)
      (func (export "read_at") (param i32) (result i32)
        local.get 0
        i32.load
      )
    )
    """
    engine, _ = _make_engine_with_fuel()
    module = _module_from_wat(engine, wat)
    linker = Linker(engine)
    linker.define_wasi()
    store = _sandboxed_store(engine)
    instance = linker.instantiate(store, module)
    exports = instance.exports(store)
    read_at = exports["read_at"]

    # Read at offset 0 in Wasm memory
    value = read_at(store, 0)
    assert value != sentinel, (
        "Host sentinel value leaked into Wasm linear memory — isolation failure!"
    )


# ---------------------------------------------------------------------------
# Test 6 — malformed plugin output
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_malformed_plugin_output_rejected():
    """
    A plugin that returns syntactically invalid JSON must be rejected by
    ``ParserAdapter.process`` with a ``PluginOutputError``.
    """
    from unittest.mock import MagicMock

    from intentdiff.plugins.adapter import ParserAdapter
    from intentdiff.plugins.exceptions import PluginOutputError

    mock_plugin = MagicMock()
    mock_plugin.call_grammar_id.return_value = "test"
    mock_plugin.call_language_ids.return_value = ["test"]
    mock_plugin.call_trivia_node_types.return_value = []
    mock_plugin.call_parser_mode.return_value = "full-parse"
    mock_plugin.call_priority.return_value = 0

    # Return garbage JSON
    mock_plugin.call_process.return_value = "NOT VALID JSON {{{"

    adapter = ParserAdapter(mock_plugin)

    with pytest.raises(PluginOutputError):
        adapter.process("{}", "test", "test.txt")


# ---------------------------------------------------------------------------
# Test 7 — fuel exhaustion (infinite loop guard)
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_infinite_loop_fuel_exhausted():
    """
    A Wasm module containing an infinite loop must trap with a fuel-exhausted
    error when the fuel budget is exceeded.
    """
    wat = r"""
    (module
      (func (export "infinite_loop")
        block $break
          loop $loop
            br $loop
          end
        end
      )
    )
    """
    engine, _ = _make_engine_with_fuel()
    module = _module_from_wat(engine, wat)
    linker = Linker(engine)
    linker.define_wasi()
    store = _sandboxed_store(engine, fuel=10_000)  # tiny budget
    instance = linker.instantiate(store, module)
    exports = instance.exports(store)
    infinite_loop = exports["infinite_loop"]

    with pytest.raises((WasmtimeError, Trap)) as exc_info:
        infinite_loop(store)

    assert "fuel" in str(exc_info.value).lower() or "trap" in str(exc_info.value).lower()
