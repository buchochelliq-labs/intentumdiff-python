"""
intentumdiff.plugins.builtins
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Entry-point callables for the built-in Wasm plugins.

Each function returns the absolute path to the corresponding ``.wasm`` file
inside the package's ``wasm/`` directory.  These functions are registered in
``pyproject.toml`` under ``[project.entry-points]``.
"""

from __future__ import annotations

from pathlib import Path

_WASM_DIR = Path(__file__).parent.parent / "wasm"


def _wasm(name: str) -> str:
    return str(_WASM_DIR / name)


def _wasm_or_generic(name: str) -> str:
    path = _WASM_DIR / name
    if path.exists():
        return str(path)
    return _wasm("generic_parser.wasm")


# ── Parser entry points ──────────────────────────────────────────────────────

def python_parser_entry() -> str:
    return _wasm("python_parser.wasm")


def sql_parser_entry() -> str:
    return _wasm("sql_parser.wasm")


def generic_parser_entry() -> str:
    return _wasm("generic_parser.wasm")


def graphql_parser_entry() -> str:
    return _wasm_or_generic("graphql_parser.wasm")


def gitignore_parser_entry() -> str:
    return _wasm_or_generic("gitignore_parser.wasm")


def ocaml_parser_entry() -> str:
    return _wasm_or_generic("ocaml_parser.wasm")


def reasonml_parser_entry() -> str:
    return _wasm_or_generic("reasonml_parser.wasm")


def latex_parser_entry() -> str:
    return _wasm_or_generic("latex_parser.wasm")


def asciidoc_parser_entry() -> str:
    return _wasm_or_generic("asciidoc_parser.wasm")


def po_parser_entry() -> str:
    return _wasm_or_generic("po_parser.wasm")


def js_ts_parser_entry() -> str:
    return _wasm("js_ts_parser.wasm")


def java_parser_entry() -> str:
    return _wasm("java_parser.wasm")


def go_parser_entry() -> str:
    return _wasm("go_parser.wasm")


def rust_parser_entry() -> str:
    return _wasm("rust_parser.wasm")


def csharp_parser_entry() -> str:
    return _wasm("csharp_parser.wasm")


def ruby_parser_entry() -> str:
    return _wasm("ruby_parser.wasm")


def php_parser_entry() -> str:
    return _wasm("php_parser.wasm")


def kotlin_parser_entry() -> str:
    return _wasm("kotlin_parser.wasm")


def cpp_parser_entry() -> str:
    return _wasm("cpp_parser.wasm")


def swift_parser_entry() -> str:
    return _wasm("swift_parser.wasm")


def bash_parser_entry() -> str:
    return _wasm("bash_parser.wasm")


def powershell_parser_entry() -> str:
    return _wasm("powershell_parser.wasm")


def elixir_parser_entry() -> str:
    return _wasm("elixir_parser.wasm")


def groovy_parser_entry() -> str:
    return _wasm("groovy_parser.wasm")


def dart_parser_entry() -> str:
    return _wasm("dart_parser.wasm")


def lua_parser_entry() -> str:
    return _wasm("lua_parser.wasm")


def xml_parser_entry() -> str:
    return _wasm("xml_parser.wasm")


def dockerfile_parser_entry() -> str:
    return _wasm("dockerfile_parser.wasm")


def vbnet_parser_entry() -> str:
    return _wasm("vbnet_parser.wasm")


def squirrel_parser_entry() -> str:
    return _wasm("squirrel_parser.wasm")


def puppet_parser_entry() -> str:
    return _wasm("puppet_parser.wasm")


def terraform_parser_entry() -> str:
    return _wasm("terraform_parser.wasm")


def delphi_parser_entry() -> str:
    return _wasm("delphi_parser.wasm")


def adf_parser_entry() -> str:
    return _wasm("adf_parser.wasm")


def databricks_parser_entry() -> str:
    return _wasm("databricks_parser.wasm")


def mdx_parser_entry() -> str:
    return _wasm("mdx_parser.wasm")


def markdown_parser_entry() -> str:
    return _wasm("markdown_parser.wasm")


def toml_parser_entry() -> str:
    return _wasm("toml_parser.wasm")


def ini_parser_entry() -> str:
    return _wasm("ini_parser.wasm")


def gomod_parser_entry() -> str:
    return _wasm("gomod_parser.wasm")


def make_parser_entry() -> str:
    return _wasm("make_parser.wasm")


def proto_parser_entry() -> str:
    return _wasm("proto_parser.wasm")


def cmake_parser_entry() -> str:
    return _wasm("cmake_parser.wasm")


def r_parser_entry() -> str:
    return _wasm("r_parser.wasm")


def haskell_parser_entry() -> str:
    return _wasm("haskell_parser.wasm")


def zig_parser_entry() -> str:
    return _wasm("zig_parser.wasm")


def scala_parser_entry() -> str:
    return _wasm("scala_parser.wasm")


def clojure_parser_entry() -> str:
    return _wasm("clojure_parser.wasm")


def perl_parser_entry() -> str:
    return _wasm("perl_parser.wasm")


def asm_parser_entry() -> str:
    return _wasm("asm_parser.wasm")


def assemblyscript_parser_entry() -> str:
    return _wasm("assemblyscript_parser.wasm")


def freebasic_parser_entry() -> str:
    return _wasm("freebasic_parser.wasm")


def odin_parser_entry() -> str:
    return _wasm("odin_parser.wasm")


def wat_parser_entry() -> str:
    return _wasm("wat_parser.wasm")


def tsql_parser_entry() -> str:
    return _wasm("tsql_parser.wasm")


def plsql_parser_entry() -> str:
    return _wasm("plsql_parser.wasm")


def abap_parser_entry() -> str:
    return _wasm("abap_parser.wasm")


def dax_parser_entry() -> str:
    return _wasm("dax_parser.wasm")


def sas_parser_entry() -> str:
    return _wasm("sas_parser.wasm")


def qsharp_parser_entry() -> str:
    return _wasm("qsharp_parser.wasm")


def postscript_parser_entry() -> str:
    return _wasm("postscript_parser.wasm")


def css_parser_entry() -> str:
    return _wasm("css_parser.wasm")


def json_parser_entry() -> str:
    return _wasm("json_parser.wasm")


def yaml_parser_entry() -> str:
    return _wasm("yaml_parser.wasm")


def html_parser_entry() -> str:
    return _wasm("html_parser.wasm")


def scss_parser_entry() -> str:
    return _wasm("scss_parser.wasm")


def vue_parser_entry() -> str:
    return _wasm("vue_parser.wasm")


def svelte_parser_entry() -> str:
    return _wasm("svelte_parser.wasm")


def astro_parser_entry() -> str:
    return _wasm("astro_parser.wasm")


# ── Renderer entry points ────────────────────────────────────────────────────

def terminal_renderer_entry() -> str:
    return _wasm("terminal_renderer.wasm")


def patch_renderer_entry() -> str:
    return _wasm("patch_renderer.wasm")


def html_renderer_entry() -> str:
    return _wasm("html_renderer.wasm")


def llm_renderer_entry() -> str:
    return _wasm("llm_renderer.wasm")
