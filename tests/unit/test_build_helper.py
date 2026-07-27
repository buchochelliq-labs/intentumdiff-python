from __future__ import annotations

import build as build_helper


def test_build_skips_cargo_build_when_intentdiff_skip_env_is_set(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv("INTENTDIFF_SKIP_WASM_BUILD", "1")
    monkeypatch.setattr(build_helper, "_cargo_build", lambda: calls.append("cargo"))
    monkeypatch.setattr(build_helper, "_copy_wasm_artifacts", lambda: calls.append("copy"))

    build_helper.build()

    assert calls == ["copy"]


def test_build_runs_cargo_build_without_skip_env(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.delenv("INTENTDIFF_SKIP_WASM_BUILD", raising=False)
    monkeypatch.setattr(build_helper, "_cargo_build", lambda: calls.append("cargo"))
    monkeypatch.setattr(build_helper, "_copy_wasm_artifacts", lambda: calls.append("copy"))

    build_helper.build()

    assert calls == ["cargo", "copy"]


def test_configure_wasm_c_toolchain_uses_zig_when_available(monkeypatch) -> None:
    monkeypatch.setattr(build_helper.shutil, "which", lambda name: "zig" if name == "zig" else None)

    env: dict[str, str] = {}
    build_helper._configure_wasm_c_toolchain(env)

    assert env["CC_wasm32_wasip2"].endswith("scripts\\zig_cc_wasm32.py")
    assert env["CC_wasm32-wasip2"] == env["CC_wasm32_wasip2"]
    assert env["AR_wasm32_wasip2"] == "zig ar"
    assert env["AR_wasm32-wasip2"] == "zig ar"
    assert env["CFLAGS_wasm32_wasip2"] == "--target=wasm32-wasi"
    assert env["CFLAGS_wasm32-wasip2"] == "--target=wasm32-wasi"


def test_configure_wasm_c_toolchain_preserves_existing_env(monkeypatch) -> None:
    monkeypatch.setattr(build_helper.shutil, "which", lambda name: "zig" if name == "zig" else None)

    env = {
        "CC_wasm32_wasip2": "custom-cc",
        "AR_wasm32_wasip2": "custom-ar",
        "CFLAGS_wasm32_wasip2": "-Oz",
    }

    build_helper._configure_wasm_c_toolchain(env)

    assert env["CC_wasm32_wasip2"] == "custom-cc"
    assert env["AR_wasm32_wasip2"] == "custom-ar"
    assert env["CFLAGS_wasm32_wasip2"] == "-Oz --target=wasm32-wasi"
