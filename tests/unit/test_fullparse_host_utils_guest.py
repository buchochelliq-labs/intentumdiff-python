"""Real imported host-utils calls; the split repo needs no core source checkout."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from intentumdiff.plugins.exceptions import PluginSandboxViolation
from intentumdiff.plugins.loader import (
    _strip_trivia_impl,
    _structural_hash_impl,
    load_plugin,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "host_utils_guest"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def guest_wasm(tmp_path_factory):
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    compressed = (FIXTURES / "host_utils_guest.wasm.gz").read_bytes()
    assert hashlib.sha256(compressed).hexdigest() == provenance["gzip_sha256"]
    payload = gzip.decompress(compressed)
    assert hashlib.sha256(payload).hexdigest() == provenance["wasm_sha256"]
    assert hashlib.sha256((FIXTURES / "cases.json").read_bytes()).hexdigest() == provenance["cases_sha256"]
    path = tmp_path_factory.mktemp("host-utils-guest") / "host_utils_guest.wasm"
    path.write_bytes(payload)
    return path


def invoke(path, operation, tree, trivia):
    # This is our checked-in test component, not an arbitrary external plugin.
    guest = load_plugin(path, fuel=2_000_000_000, trusted=True)
    assert guest.call_parser_mode() == "full-parse"
    request = json.dumps({"operation": operation, "tree": tree, "trivia": trivia})
    return guest.call_process(request, "host-utils-test", "fixture.json")


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_fullparse_guest_matches_independent_contract(guest_wasm, case):
    def direct():
        if case["operation"] == "strip":
            return json.loads(_strip_trivia_impl(case["tree"], case["trivia"]))
        return _structural_hash_impl(case["tree"])

    if "error_contains" in case:
        with pytest.raises(ValueError, match=case["error_contains"]):
            direct()
        with pytest.raises(PluginSandboxViolation, match=case["error_contains"]):
            invoke(guest_wasm, case["operation"], case["tree"], case["trivia"])
    else:
        assert direct() == case["expected"]
        assert json.loads(invoke(guest_wasm, case["operation"], case["tree"], case["trivia"])) == case["expected"]


@pytest.mark.parametrize("operation", ["ignore-strip-error", "ignore-hash-error"])
def test_fullparse_guest_cannot_hide_host_error(guest_wasm, operation):
    with pytest.raises(PluginSandboxViolation, match="invalid JSON"):
        invoke(guest_wasm, operation, "not JSON", [])


@pytest.mark.parametrize("operation", ["strip", "hash"])
@pytest.mark.parametrize("limit", ["byte", "depth", "nodes"])
def test_fullparse_guest_tree_limits(guest_wasm, operation, limit):
    if limit == "byte":
        tree, message = " " * (8 * 1024 * 1024 + 1), "byte limit"
    elif limit == "depth":
        tree, message = "[" * 257 + "0" + "]" * 257, "nesting depth"
    else:
        tree, message = '{"children":[' + ",".join(["{}"] * 1_000_000) + "]}", "node count"
    with pytest.raises(PluginSandboxViolation, match=message):
        invoke(guest_wasm, operation, tree, [])


@pytest.mark.parametrize("trivia,message", [
    (["comment"] * 1025, "type count"),
    (["x" * 257], "type exceeds"),
    (["x" * 256] * 257, "payload exceeds"),
])
def test_fullparse_guest_trivia_limits(guest_wasm, trivia, message):
    with pytest.raises(PluginSandboxViolation, match=message):
        invoke(guest_wasm, "strip", "{}", trivia)
