"""Guardrail interpretation must be available through the shared Rust ABI."""
import pytest
from intentumdiff.rust_core import _c_abi_call


def test_shared_policy_normalizes_known_source():
    rules = _c_abi_call('parse_guardrail_policy', 'protected:\n  - language: JSON\n    path: " .Secret.Token. "\n')
    assert rules == [{'rule_id': 'guardrail.1', 'severity': 'important', 'language': 'json',
                      'path': 'secret.token', 'message': 'Protected path secret.token changed', 'files': []}]


@pytest.mark.parametrize('source', ['[]', 'guardrails: []', 'protected: nope'])
def test_shared_policy_rejects_invalid_structure(source):
    with pytest.raises(ValueError, match='mapping|list'):
        _c_abi_call('parse_guardrail_policy', source)


def test_source_judged_policy_corpus():
    import json
    import os
    import subprocess
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / "fixtures/guardrail_policy.json").read_text(encoding="utf-8"))
    for case in cases:
        try:
            actual = {"result": _c_abi_call("parse_guardrail_policy", case["source"])}
        except ValueError as exc:
            actual = {"error": str(exc)}
        if "error" in case:
            assert case["error"] in actual["error"], case["name"]
        else:
            assert actual == {"result": case["expected"]}, case["name"]
        probe = os.environ.get("INTENTUMDIFF_NATIVE_PROBE")
        if probe:
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler": "parse_guardrail_policy", "source": case["source"]}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            assert native == actual, case["name"]
