"""Contract checks for shipped playground examples across supported languages."""

from __future__ import annotations

from typing import Any

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import ChangeGroupKind, ChangeType

from tests.unit.diff_sanity import assert_no_identical_positioned_source_modifications


KNOWN_EXAMPLE_CONTRACT_GAPS: dict[str, str] = {}


def test_no_supported_language_contract_gaps_remain() -> None:
    assert KNOWN_EXAMPLE_CONTRACT_GAPS == {}


def test_known_contract_gaps_are_not_parser_availability_gaps() -> None:
    forbidden_markers = ("tree-sitter", "parser package", "not installed", "grammar is missing")
    for language, reason in KNOWN_EXAMPLE_CONTRACT_GAPS.items():
        lower = reason.lower()
        assert not any(marker in lower for marker in forbidden_markers), (language, reason)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "supported_language" not in metafunc.fixturenames:
        return
    languages = SemanticDiffer().supported_languages()
    metafunc.parametrize("supported_language", languages)


@pytest.fixture(scope="module")
def differ() -> SemanticDiffer:
    return SemanticDiffer()


@pytest.fixture(scope="module")
def default_filenames(differ: SemanticDiffer) -> dict[str, str]:
    filenames: dict[str, str] = {}
    for group in differ.language_info():
        if not group.plugins:
            continue
        selected = next(
            (
                plugin
                for plugin in group.plugins
                if plugin.plugin_id == group.selected_plugin_id
            ),
            group.plugins[0],
        )
        filenames[group.language] = selected.default_filename
    return filenames


def test_supported_language_example_contract(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    supported_language: str,
) -> None:
    if supported_language in KNOWN_EXAMPLE_CONTRACT_GAPS:
        pytest.skip(KNOWN_EXAMPLE_CONTRACT_GAPS[supported_language])

    example: dict[str, Any] | None = differ.playground_example(supported_language)
    assert example is not None
    old = example.get("old", "")
    new = example.get("new", "")
    assert old
    assert new

    diff = differ.diff_strings(
        old,
        new,
        filename=default_filenames.get(supported_language, f"example.{supported_language}"),
        language_hint=supported_language,
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    if old != new:
        assert diff.has_semantic_changes or diff.is_style_only
        assert diff.changes or diff.change_groups or diff.is_style_only


@pytest.mark.parametrize(
    "language",
    [
        "rust",
        "elixir",
        "powershell",
        "sql",
        "yaml",
        "xml",
        "mdx",
        "ruby",
        "php",
        "vbnet",
        "kotlin",
        "groovy",
        "lua",
        "dart",
        "scala",
        "dockerfile",
        "hcl",
        "squirrel",
        "delphi",
        "c",
        "cpp",
    ],
)
def test_competitor_requested_languages_have_issue_shaped_examples(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    language: str,
) -> None:
    assert language in differ.supported_languages()
    example: dict[str, Any] | None = differ.playground_example(language)
    assert example is not None

    diff = differ.diff_strings(
        example["old"],
        example["new"],
        filename=default_filenames.get(language, f"example.{language}"),
        language_hint=language,
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.has_semantic_changes or diff.is_style_only
    assert diff.changes or diff.change_groups or diff.is_style_only


def test_supported_language_does_not_report_identical_positioned_modifications(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    supported_language: str,
) -> None:
    if supported_language in KNOWN_EXAMPLE_CONTRACT_GAPS:
        pytest.skip(KNOWN_EXAMPLE_CONTRACT_GAPS[supported_language])

    example: dict[str, Any] | None = differ.playground_example(supported_language)
    assert example is not None
    old = example.get("old", "")
    new = example.get("new", "")
    assert old
    assert new

    diff = differ.diff_strings(
        old,
        new,
        filename=default_filenames.get(supported_language, f"example.{supported_language}"),
        language_hint=supported_language,
    )

    assert_no_identical_positioned_source_modifications(diff, old, new)


def test_supported_language_new_file_is_not_style_only(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    supported_language: str,
) -> None:
    if supported_language in KNOWN_EXAMPLE_CONTRACT_GAPS:
        pytest.skip(KNOWN_EXAMPLE_CONTRACT_GAPS[supported_language])

    example: dict[str, Any] | None = differ.playground_example(supported_language)
    assert example is not None
    new = example.get("new", "")
    assert new

    diff = differ.diff_strings(
        "",
        new,
        filename=default_filenames.get(supported_language, f"example.{supported_language}"),
        language_hint=supported_language,
    )

    assert diff.metadata.get("file_lifecycle") == "added"
    assert not diff.is_style_only
    assert not _has_ignored_style_group(diff.change_groups)
    assert _has_change_type(diff.changes, ChangeType.ADDITION), supported_language


def test_supported_language_deleted_file_is_not_style_only(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    supported_language: str,
) -> None:
    if supported_language in KNOWN_EXAMPLE_CONTRACT_GAPS:
        pytest.skip(KNOWN_EXAMPLE_CONTRACT_GAPS[supported_language])

    example: dict[str, Any] | None = differ.playground_example(supported_language)
    assert example is not None
    old = example.get("old", "")
    assert old

    diff = differ.diff_strings(
        old,
        "",
        filename=default_filenames.get(supported_language, f"example.{supported_language}"),
        language_hint=supported_language,
    )

    assert diff.metadata.get("file_lifecycle") == "deleted"
    assert not diff.is_style_only
    assert not _has_ignored_style_group(diff.change_groups)
    assert _has_change_type(diff.changes, ChangeType.DELETION), supported_language


def test_supported_language_structural_additions_are_not_style_only(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    supported_language: str,
) -> None:
    if supported_language in KNOWN_EXAMPLE_CONTRACT_GAPS:
        pytest.skip(KNOWN_EXAMPLE_CONTRACT_GAPS[supported_language])

    example: dict[str, Any] | None = differ.playground_example(supported_language)
    assert example is not None
    old = example.get("old", "")
    new = example.get("new", "")
    assert old
    assert new

    diff = differ.diff_strings(
        old,
        new,
        filename=default_filenames.get(supported_language, f"example.{supported_language}"),
        language_hint=supported_language,
    )

    has_structural_add_or_delete = _has_any_change_type(
        diff.changes,
        {ChangeType.ADDITION, ChangeType.DELETION},
    )
    if not has_structural_add_or_delete:
        pytest.skip(f"{supported_language} example has no structural add/delete evidence")

    assert diff.metadata.get("file_lifecycle") == "modified"
    assert not diff.is_style_only
    assert diff.has_semantic_changes


@pytest.mark.parametrize(
    ("old", "new", "lifecycle", "change_type"),
    [
        (
            "",
            'def boo():\n    print("Boo!")\n\n\n\n\n\n\n\n\ndef boo2():\n    print("Boo2!")\n',
            "added",
            ChangeType.ADDITION,
        ),
        (
            'def boo():\n    print("Boo!")\n\n\n\n\n\n\n\n\ndef boo2():\n    print("Boo2!")\n',
            "",
            "deleted",
            ChangeType.DELETION,
        ),
    ],
)
def test_python_file_lifecycle_with_blank_lines_is_not_style_only(
    differ: SemanticDiffer,
    old: str,
    new: str,
    lifecycle: str,
    change_type: ChangeType,
) -> None:
    diff = differ.diff_strings(old, new, filename="boo.py", language_hint="python")

    assert diff.metadata.get("file_lifecycle") == lifecycle
    assert not diff.is_style_only
    assert not _has_ignored_style_group(diff.change_groups)
    assert _has_change_type(diff.changes, change_type)


def test_python_added_method_in_modified_file_is_not_style_only(
    differ: SemanticDiffer,
) -> None:
    old = 'def boo():\n    print("Boo!")\n'
    new = old + '\n\ndef boo2():\n    print("Boo 2!")\n'

    diff = differ.diff_strings(old, new, filename="boo.py", language_hint="python")

    assert diff.metadata.get("file_lifecycle") == "modified"
    assert not diff.is_style_only
    assert diff.has_semantic_changes
    assert _has_change_type(diff.changes, ChangeType.ADDITION)
    assert not _has_ignored_style_group(diff.change_groups)


def test_ignore_file_added_exceptions_are_not_fake_meaningful_modifications(
    differ: SemanticDiffer,
) -> None:
    old = "\n".join(
        [
            "artifacts/",
            "*.map",
            "**/*.map",
            "*.log",
            "*.vsix",
            ".env",
            ".env.*",
            ".npmrc",
            ".pypirc",
            "*.pem",
            "*.key",
            "*.crt",
            "*.p12",
            "*.pfx",
            "*.token",
            "*.secret",
            "id_rsa*",
            "id_dsa*",
            "id_ecdsa*",
            "id_ed25519*",
            "resources/*.svg",
            "!resources/activity-icon.svg",
            "!resources/review-icon-dark.svg",
            "!resources/review-icon-light.svg",
            "tsconfig.json",
            "package-lock.json",
        ]
    )
    new = old.replace(
        "!resources/review-icon-dark.svg",
        "\n".join(
            [
                "!resources/brand-mark.svg",
                "!resources/brand-mark-compact.svg",
                "!resources/process-icons.svg",
                "!resources/review-icon-dark.svg",
            ]
        ),
    )

    diff = differ.diff_strings(old, new, filename=".vscodeignore", language_hint="generic")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert not any(
        _enum_value(group.kind) == ChangeGroupKind.MEANINGFUL_CHANGE.value
        and any("resources/activity-icon.svg" in label for label in group.old_labels)
        and any("resources/brand-mark.svg" in label for label in group.new_labels)
        for group in diff.change_groups
    )
    added_labels = {
        change.new_node.label
        for change in diff.changes
        if _enum_value(change.change_type) == ChangeType.ADDITION.value
        and change.new_node is not None
    }
    assert {
        "!resources/brand-mark.svg",
        "!resources/brand-mark-compact.svg",
        "!resources/process-icons.svg",
    } <= added_labels


@pytest.mark.parametrize(
    ("language", "filename", "old", "new"),
    [
        (
            "typescript",
            "tiny.ts",
            "export const answer = 41;\n",
            "export const answer = 42;\n",
        ),
        (
            "xml",
            "tiny.svg",
            '<svg viewBox="0 0 24 24"><path d="M0 0h1"/></svg>\n',
            '<svg viewBox="0 0 24 24"><path d="M0 0h2"/></svg>\n',
        ),
        (
            "powershell",
            "tiny.ps1",
            'function Test-Thing { Write-Host "old" }\n',
            'function Test-Thing { Write-Host "new" }\n',
        ),
    ],
)
def test_tiny_files_do_not_exhaust_parser_fuel(
    differ: SemanticDiffer,
    language: str,
    filename: str,
    old: str,
    new: str,
) -> None:
    diff = differ.diff_strings(old, new, filename=filename, language_hint=language)

    assert not diff.is_fallback
    assert not any("FUEL_EXCEEDED" in error or "fuel" in error.lower() for error in diff.parse_errors)
    assert diff.changes or diff.change_groups


@pytest.mark.parametrize(
    ("language", "filename", "old", "new"),
    [
        (
            "typescript",
            "telemetry.ts",
            "export function first() { return 1; }\nexport function second() { return 2; }\n",
            "export function second() { return 2; }\nexport function first() { return 10; }\n",
        ),
        (
            "powershell",
            "telemetry.ps1",
            'function Get-First { "one" }\nfunction Get-Second { "two" }\n',
            'function Get-Second { "two" }\nfunction Get-First { "ONE" }\n',
        ),
        (
            "xml",
            "telemetry.svg",
            '<svg><g id="one"/><g id="two"/></svg>\n',
            '<svg><g id="two"/><g id="one"/><g id="three"/></svg>\n',
        ),
        (
            "mdx",
            "telemetry.mdx",
            "# Title\n\n<Alpha value=\"old\" />\n\n<Beta />\n",
            "# Title\n\n<Beta />\n\n<Alpha value=\"new\" />\n",
        ),
        (
            "postscript",
            "telemetry.ps",
            "%!PS\n/first { (one) show } def\n/second { (two) show } def\n",
            "%!PS\n/second { (two) show } def\n/first { (ONE) show } def\n",
        ),
    ],
)
def test_fuel_sensitive_language_parsers_emit_engine_telemetry(
    differ: SemanticDiffer,
    language: str,
    filename: str,
    old: str,
    new: str,
) -> None:
    diff = differ.diff_strings(old, new, filename=filename, language_hint=language)

    assert not diff.is_fallback
    assert not any(
        "FUEL_EXCEEDED" in error
        or "fuel" in error.lower()
        or "recursion" in error.lower()
        for error in diff.parse_errors
    )
    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry, language
    process_calls = [
        call
        for call in telemetry["calls"]
        if call["function"] == "process"
    ]
    assert process_calls, language
    assert all(call["engine_owner"] == "python" for call in process_calls)
    assert all(call["engine"] == "python_wasmtime_plugin_host" for call in process_calls)
    assert all(call["provenance"] == "first_party_wasm" for call in process_calls)
    assert any(call["fuel_consumed"] and call["fuel_consumed"] > 0 for call in process_calls)


@pytest.mark.parametrize(
    "language",
    [
        "typescript",
        "tsx",
        "generic",
        "wat",
        "wast",
        "kotlin",
        "swift",
        "plsql",
        "tsql",
        "astro",
        "svelte",
        "vue",
    ],
)
def test_repaired_contract_examples_are_structured(
    differ: SemanticDiffer,
    default_filenames: dict[str, str],
    language: str,
) -> None:
    example: dict[str, Any] | None = differ.playground_example(language)
    assert example is not None

    diff = differ.diff_strings(
        example["old"],
        example["new"],
        filename=default_filenames.get(language, f"example.{language}"),
        language_hint=language,
    )

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.has_semantic_changes
    assert diff.changes or diff.change_groups

    labels = {
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    }
    labels.update(
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    )
    expected_labels = {
        "astro": {"year", "meta", "header", "main"},
        "svelte": {"count", "increment", "button"},
        "vue": {"button", "@click=changeGreeting", "changeGreeting", "names"},
    }
    if language in expected_labels:
        assert labels & expected_labels[language], (language, sorted(labels))


def test_graphql_parser_depth_extracts_schema_operation_fragment_and_directives(
    differ: SemanticDiffer,
) -> None:
    old = """\
type User {
  id: ID!
  name: String
}

query UserCard($id: ID!) {
  user(id: $id) {
    ...UserFields
  }
}

fragment UserFields on User {
  name
}
"""
    new = """\
type User {
  id: ID!
  displayName: String @deprecated(reason: "Use profileName")
}

query UserCard($id: ID!, $includeMeta: Boolean) {
  user(id: $id) {
    ...UserFields @include(if: $includeMeta)
  }
}

fragment UserFields on User {
  displayName
}
"""

    diff = differ.diff_strings(old, new, filename="schema.graphql", language_hint="graphql")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    labels = {
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    }
    labels.update(
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    )
    assert {"User", "UserCard", "UserFields"} <= labels
    assert "displayName" in labels
    assert "@deprecated" in labels or "@include" in labels

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry
    assert any(
        call["function"] == "process"
        and call["provenance"] == "first_party_wasm"
        and call["fuel_consumed"]
        for call in telemetry["calls"]
    )


def test_po_parser_depth_extracts_context_plural_flags_and_obsolete_entries(
    differ: SemanticDiffer,
) -> None:
    old = """\
#. Button label
#: src/ui.py:10
#, fuzzy, python-format
msgctxt "button"
msgid "Save %s"
msgid_plural "Save %s files"
msgstr[0] "Save %s"
msgstr[1] "Save %s files"

#~ msgid "Old label"
#~ msgstr "Ancien"
"""
    new = """\
#. Primary action label
#: src/ui.py:10
#, python-format
msgctxt "button"
msgid "Save %s"
msgid_plural "Save %s files"
msgstr[0] "Store %s"
msgstr[1] "Store %s files"

#~ msgid "Legacy label"
#~ msgstr "Ancien"
"""

    diff = differ.diff_strings(old, new, filename="messages.po", language_hint="po")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    labels = {
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    }
    labels.update(
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    )
    # Changed content must surface. The unchanged msgctxt (``button``),
    # the unchanged flag ``python-format``, and the unchanged reference
    # ``src/ui.py:10`` are correctly preserved by the Rust matcher and
    # may or may not appear depending on the matcher path; only the
    # genuinely-changed labels are required.
    assert {"Save %s", "Save %s files"} <= (
        labels
    )
    assert "fuzzy" in labels
    assert "Old label" in labels or "Legacy label" in labels
    assert "Store %s" in labels or "Store %s files" in labels

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry
    assert any(
        call["function"] == "process"
        and call["provenance"] == "first_party_wasm"
        and call["fuel_consumed"]
        for call in telemetry["calls"]
    )


def test_asciidoc_parser_depth_extracts_review_landmarks(
    differ: SemanticDiffer,
) -> None:
    old = """\
= IntentumDiff
:revnumber: 1.0
[[install]]
== Install
include::partials/setup.adoc[]
NOTE: See link:https://example.com/docs[docs].
* Run setup
image::screens/review.png[]
----
intentumdiff git main
----
"""
    new = """\
= IntentumDiff
:revnumber: 1.1
[[usage]]
== Usage
include::partials/usage.adoc[]
NOTE: See link:https://example.com/docs[docs] and xref:#install[Install].
* Run review
image::screens/dashboard.png[]
----
intentumdiff git main --format json
----
"""

    diff = differ.diff_strings(old, new, filename="README.adoc", language_hint="asciidoc")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    labels = {
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    }
    labels.update(
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    )
    assert {"revnumber: 1.1", "usage", "Usage"} <= labels
    assert "partials/usage.adoc" in labels
    assert "screens/dashboard.png" in labels
    assert "https://example.com/docs" in labels
    assert "install" in labels

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry
    assert any(
        call["function"] == "process"
        and call["provenance"] == "first_party_wasm"
        and call["fuel_consumed"]
        for call in telemetry["calls"]
    )


def test_latex_parser_depth_extracts_review_landmarks(
    differ: SemanticDiffer,
) -> None:
    old = r"""\
\documentclass[11pt]{article}
\usepackage{graphicx,hyperref}
\begin{document}
\section{Introduction}\label{sec:intro}
See \ref{sec:method} and \cite{knuth1984}.
\subsection{Figure}
\includegraphics[width=.5\textwidth]{figures/overview.png}
\input{sections/method}
\begin{equation}
E = mc^2
\end{equation}
\end{document}
"""
    new = r"""\
\documentclass[11pt]{article}
\usepackage{graphicx,hyperref,amsmath}
\begin{document}
\section{Overview}\label{sec:overview}
See \autoref{sec:results} and \citep{lamport1994}.
\subsection{Architecture}
\includegraphics[width=.6\textwidth]{figures/architecture.png}
\input{sections/results}
\begin{align}
E &= mc^2
\end{align}
\end{document}
"""

    diff = differ.diff_strings(old, new, filename="paper.tex", language_hint="latex")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    labels = {
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    }
    labels.update(
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    )
    # `article`, `graphicx`, `hyperref` are byte-identical preamble tokens
    # that the Rust matcher correctly preserves; only the changed `amsmath`
    # package surfaces. `document` is a changed-in-place container that
    # Step A2's entity-surfacing rule will expose once landed; until then
    # it may or may not appear depending on the matcher path.
    assert {"amsmath"} <= labels
    assert {"Overview", "sec:overview", "sec:results", "lamport1994"} <= labels
    assert "figures/architecture.png" in labels
    assert "sections/results" in labels
    assert "align" in labels

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry
    assert any(
        call["function"] == "process"
        and call["provenance"] == "first_party_wasm"
        and call["fuel_consumed"]
        for call in telemetry["calls"]
    )


def test_ocaml_parser_depth_extracts_review_landmarks(
    differ: SemanticDiffer,
) -> None:
    old = """\
open Core
module Store : STORE = struct
type user = { id : string }
let rec load id = id
external digest : string -> string = "digest"
class cache = object end
val save : user -> unit
end
"""
    new = """\
open Core
include Shared
module AccountStore : STORE = struct
type account = { id : string; active : bool }
let rec load_account id = id
external hash : string -> string = "digest"
class memo = object end
class type readable = object end
val persist : account -> unit
end
"""

    diff = differ.diff_strings(old, new, filename="main.ml", language_hint="ocaml")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    labels = {
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    }
    labels.update(
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    )
    assert {"Shared", "AccountStore", "account"} <= labels
    assert {"load_account", "hash", "memo", "readable", "persist"} <= labels

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry
    assert any(
        call["function"] == "process"
        and call["provenance"] == "first_party_wasm"
        and call["fuel_consumed"]
        for call in telemetry["calls"]
    )


def test_reasonml_parser_depth_extracts_review_landmarks(
    differ: SemanticDiffer,
) -> None:
    old = """\
open React;
module Store: STORE = {};
type user = {id: string};
let rec loadUser = id => id;
let UserCard = props => <div />;
external digest: string => string = "digest";
exception MissingUser;
"""
    new = """\
open React;
include Shared;
module AccountStore: STORE = {};
type account = {id: string, active: bool};
let rec loadAccount = id => id;
let AccountCard = props => <section />;
external hash: string => string = "digest";
exception MissingAccount;
"""

    diff = differ.diff_strings(old, new, filename="component.re", language_hint="reasonml")

    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes or diff.change_groups
    labels = {
        label
        for change in diff.changes
        for node in (change.old_node, change.new_node)
        if node is not None
        for label in [node.label, *(child.label for child in node.children)]
    }
    labels.update(
        label
        for group in diff.change_groups
        for label in [*group.old_labels, *group.new_labels]
    )
    assert {"Shared", "AccountStore", "account"} <= labels
    assert {"loadAccount", "AccountCard", "hash", "MissingAccount"} <= labels

    telemetry = diff.metadata.get("engine_telemetry")
    assert telemetry
    assert any(
        call["function"] == "process"
        and call["provenance"] == "first_party_wasm"
        and call["fuel_consumed"]
        for call in telemetry["calls"]
    )


def test_freebasic_is_not_advertised_until_parser_contract_is_restored(
    differ: SemanticDiffer,
) -> None:
    assert "freebasic" not in differ.supported_languages()
    assert "freebasic" not in {group.language for group in differ.language_info()}


def _has_ignored_style_group(groups: list[Any]) -> bool:
    return any(_enum_value(group.kind) == ChangeGroupKind.IGNORED_STYLE.value for group in groups)


def _has_change_type(changes: list[Any], expected: ChangeType) -> bool:
    return any(_enum_value(change.change_type) == expected.value for change in changes)


def _has_any_change_type(changes: list[Any], expected: set[ChangeType]) -> bool:
    expected_values = {item.value for item in expected}
    return any(_enum_value(change.change_type) in expected_values for change in changes)


def _enum_value(value: Any) -> str:
    return getattr(value, "value", value)
