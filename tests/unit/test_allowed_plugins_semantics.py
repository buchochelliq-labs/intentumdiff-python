"""#311 (part): discovery uses the same allowlist rule as core's actual parser selection.

Core's selection (filename_selection / content_detection) matches grammar IDs and keeps
None ("no allowlist": every parser) distinct from [] ("allow nothing"). Discovery must agree,
or the playground lists languages that every diff then refuses.
"""

from __future__ import annotations

import pytest

from intentumdiff import DiffConfig, SemanticDiffer
from intentumdiff.plugins.exceptions import PluginNotFoundError


@pytest.fixture(scope="module")
def outcomes() -> dict:
    results = {}
    for key, allowed in (("none", None), ("empty", []), ("grammar", ["python"])):
        differ = SemanticDiffer(DiffConfig(allowed_plugins=allowed))
        languages = differ._registry.language_ids()
        info = {group.language for group in differ._registry.language_info()}
        try:
            selected = differ.diff_strings("x = 1\n", "x = 2\n", "a.py").language
        except PluginNotFoundError:
            selected = None
        results[key] = (languages, info, selected)
    return results


def test_no_allowlist_lists_and_selects_everything(outcomes) -> None:
    languages, info, selected = outcomes["none"]
    assert "python" in languages and "python" in info and selected == "python"


def test_an_empty_allowlist_lists_nothing_because_core_selects_nothing(outcomes) -> None:
    languages, info, selected = outcomes["empty"]
    assert selected is None
    assert languages == [] and info == set()


def test_a_grammar_id_allowlist_agrees_between_listing_and_selection(outcomes) -> None:
    languages, info, selected = outcomes["grammar"]
    assert languages == ["python"] and info == {"python"} and selected == "python"
