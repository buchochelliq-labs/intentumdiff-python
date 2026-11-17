"""#311: one allowlist rule, core's ``parser_allowlist_matches``, for discovery and selection.

Core's rule: ``None`` allows every parser, ``[]`` allows none, otherwise an entry selects a
parser when it exactly equals its grammar id, plugin/catalogue id, a catalogue alias or a
declared language id. Discovery (``language_ids``/``language_info``), actual diff selection
(core filename_selection) and the certified Python batch gate all ask core, so they agree.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from intentumdiff import DiffConfig, SemanticDiffer, rust_core
from intentumdiff.plugins.exceptions import PluginNotFoundError

_PYTHON_PLUGIN_ID = "intentumdiff-python:python:python"
_CASES = {
    "none": None,
    "empty": [],
    "grammar": ["python"],
    "plugin-id": [_PYTHON_PLUGIN_ID],
    "language": ["typescript"],
    "not-an-identifier": ["python_parser"],
}


def _select(differ: SemanticDiffer, filename: str) -> str | None:
    try:
        return differ.diff_strings("x = 1\n", "x = 2\n", filename).language
    except PluginNotFoundError:
        return None


@pytest.fixture(scope="module")
def outcomes() -> dict:
    results = {}
    loaded = None  # load the parser inventory once; each config's filter is what differs
    for key, allowed in _CASES.items():
        differ = SemanticDiffer(DiffConfig(allowed_plugins=allowed))
        if loaded is None:
            loaded = differ._registry.parsers
        differ._registry._parsers = loaded
        results[key] = SimpleNamespace(
            languages=differ._registry.language_ids(),
            info={group.language for group in differ._registry.language_info()},
            py=_select(differ, "a.py"),
            ts=_select(differ, "a.ts"),
            batch_skip=differ._rust_core_python_batch_skip_reason(),
        )
    return results


def test_no_allowlist_lists_and_selects_everything(outcomes) -> None:
    o = outcomes["none"]
    assert {"python", "typescript"} <= set(o.languages) and "python" in o.info
    assert (o.py, o.ts, o.batch_skip) == ("python", "typescript", None)


def test_an_empty_allowlist_lists_and_selects_nothing(outcomes) -> None:
    o = outcomes["empty"]
    assert (o.languages, o.info, o.py, o.ts) == ([], set(), None, None)
    assert o.batch_skip == "Python parser is not allowed by plugin policy"


@pytest.mark.parametrize("key", ["grammar", "plugin-id"])
def test_grammar_and_opaque_plugin_ids_select_the_python_parser(outcomes, key) -> None:
    o = outcomes[key]
    assert (o.languages, o.info, o.py, o.ts, o.batch_skip) == (
        ["python"], {"python"}, "python", None, None)


def test_a_language_id_selects_its_parser_everywhere(outcomes) -> None:
    o = outcomes["language"]
    assert "typescript" in o.languages and "typescript" in o.info and "python" not in o.languages
    assert (o.py, o.ts) == (None, "typescript")
    assert o.batch_skip == "Python parser is not allowed by plugin policy"


def test_a_name_that_is_no_parser_identifier_selects_nothing(outcomes) -> None:
    # The batch gate used to accept "python_parser"; core's rule, which selection uses, does not.
    o = outcomes["not-an-identifier"]
    assert (o.languages, o.py, o.ts) == ([], None, None)
    assert o.batch_skip == "Python parser is not allowed by plugin policy"


def test_discovery_passes_cores_verdict_through(monkeypatch) -> None:
    calls = []

    def canned(allowed, identity):
        calls.append((allowed, identity))
        return {"matches": identity["grammar_id"] == "sql", "matched_by": None}

    differ = SemanticDiffer(DiffConfig(allowed_plugins=["anything"]))
    parsers = differ._registry.parsers
    monkeypatch.setattr(rust_core, "parser_allowlist_matches", canned)
    sql = sorted({lang for p in parsers if p.grammar_id == "sql" for lang in p.language_ids})
    assert differ._registry.language_ids() == sql
    assert len(calls) == len(parsers)
    assert all(allowed == ["anything"] and set(identity) == {"plugin_id", "grammar_id", "languages"}
               for allowed, identity in calls)


@pytest.mark.parametrize(
    "result",
    [{}, {"matches": "yes", "matched_by": None}, {"matches": True, "matched_by": None},
     {"matches": False, "matched_by": "grammar_id"}, {"matches": True}],
)
def test_a_malformed_core_answer_raises(monkeypatch, result) -> None:
    backend = SimpleNamespace(parser_allowlist_matches=lambda *_args: json.dumps(result))
    monkeypatch.setattr(rust_core, "_load_backend", lambda: backend)
    with pytest.raises(RuntimeError, match="parser_allowlist_matches"):
        rust_core.parser_allowlist_matches(None, {"grammar_id": "python"})


@pytest.mark.parametrize(
    ("allowed", "expected"),
    [(None, (True, "unrestricted")), ([], (False, None)), (["python"], (True, "grammar_id")),
     ([_PYTHON_PLUGIN_ID], (True, "plugin_id")), (["py"], (True, "alias")),
     (["py3"], (True, "language")), (["Python"], (False, None))],
)
def test_real_core_rule(allowed, expected) -> None:
    identity = {"plugin_id": _PYTHON_PLUGIN_ID, "grammar_id": "python",
                "languages": ["python", "py3"], "aliases": ["py"]}
    result = rust_core.parser_allowlist_matches(allowed, identity)
    assert (result["matches"], result["matched_by"]) == expected
