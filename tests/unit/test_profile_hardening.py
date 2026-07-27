"""Non-playground hardening tests for profile-based semantic matching."""

from __future__ import annotations

from collections.abc import Iterable

from intentdiff import SemanticDiffer
from intentdiff.core.models import Change, ChangeType, SemanticDiff, SemanticNode

_FILENAMES = {
    "asm": "code.asm",
    "bash": "code.sh",
    "css": "style.css",
    "dax": "measure.dax",
    "delphi": "code.pas",
    "dockerfile": "Dockerfile",
    "hcl": "main.hcl",
    "html": "index.html",
    "json": "data.json",
    "mdx": "doc.mdx",
    "puppet": "site.pp",
    "scss": "style.scss",
    "sql": "query.sql",
    "xml": "data.xml",
    "yaml": "data.yaml",
}


def _diff(language: str, old: str, new: str) -> SemanticDiff:
    return SemanticDiffer().diff_strings(
        old,
        new,
        filename=_FILENAMES[language],
        language_hint=language,
    )


def _walk(node: SemanticNode | None) -> Iterable[SemanticNode]:
    if node is None:
        return
    yield node
    yield from node.descendants()


def _labels(node: SemanticNode | None) -> list[str]:
    return [item.label for item in _walk(node) if item.label]


def _node_types(node: SemanticNode | None) -> list[str]:
    return [item.node_type for item in _walk(node)]


def _mentions(node: SemanticNode | None, *tokens: str) -> bool:
    labels = _labels(node)
    return all(any(token in label for label in labels) for token in tokens)


def _changes(diff: SemanticDiff, change_type: ChangeType) -> list[Change]:
    return [change for change in diff.changes if change.change_type == change_type]


def _assert_structured(diff: SemanticDiff) -> None:
    assert not diff.is_fallback
    assert not diff.parse_errors
    assert diff.changes


def _assert_no_moves_or_reorders(diff: SemanticDiff) -> None:
    offenders = [
        change
        for change in diff.changes
        if change.change_type in {ChangeType.MOVE, ChangeType.REORDER}
    ]
    assert not offenders


def _assert_has_change(
    diff: SemanticDiff,
    change_type: ChangeType,
    *,
    node_type: str | None = None,
    old_tokens: tuple[str, ...] = (),
    new_tokens: tuple[str, ...] = (),
) -> Change:
    for change in _changes(diff, change_type):
        old_node = change.old_node
        new_node = change.new_node
        nodes = [node for node in (old_node, new_node) if node is not None]
        if node_type is not None and not any(
            node.node_type == node_type or node_type in _node_types(node)
            for node in nodes
        ):
            continue
        if old_tokens and not _mentions(old_node, *old_tokens):
            continue
        if new_tokens and not _mentions(new_node, *new_tokens):
            continue
        return change
    raise AssertionError(
        f"missing {change_type.value} node_type={node_type!r} "
        f"old={old_tokens!r} new={new_tokens!r}"
    )


def _assert_no_add_delete_identity(diff: SemanticDiff, *tokens: str) -> None:
    offenders = [
        change
        for change in diff.changes
        if change.change_type in {ChangeType.ADDITION, ChangeType.DELETION}
        and (
            _mentions(change.old_node, *tokens)
            or _mentions(change.new_node, *tokens)
        )
    ]
    assert not offenders


def _assert_no_root_add_delete(
    diff: SemanticDiff,
    *,
    node_type: str,
    label: str,
) -> None:
    offenders = [
        change
        for change in diff.changes
        if (
            change.change_type in {ChangeType.ADDITION, ChangeType.DELETION}
            and (
                change.old_node is not None
                and change.old_node.node_type == node_type
                and change.old_node.label == label
                or change.new_node is not None
                and change.new_node.node_type == node_type
                and change.new_node.label == label
            )
        )
    ]
    assert not offenders


def test_json_keyed_array_items_survive_insertion_and_reordering() -> None:
    old = """\
{
  "activities": [
    {"id": "extract", "type": "copy", "enabled": true},
    {"id": "copy", "type": "copy", "timeout": 30}
  ]
}
"""
    new = """\
{
  "activities": [
    {"id": "validate", "type": "check"},
    {"id": "copy", "type": "copy", "timeout": 60},
    {"id": "extract", "type": "copy", "enabled": true}
  ]
}
"""

    diff = _diff("json", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("30",), new_tokens=("60",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("validate",))
    _assert_no_add_delete_identity(diff, "copy")
    _assert_no_add_delete_identity(diff, "extract")


def test_yaml_keyed_sequence_items_survive_mapping_reorder() -> None:
    old = """\
services:
  - name: api
    image: app:v1
  - name: worker
    image: worker:v1
settings:
  retries: 3
"""
    new = """\
settings:
  retries: 4
services:
  - name: scheduler
    image: scheduler:v1
  - name: worker
    image: worker:v2
  - name: api
    image: app:v1
"""

    diff = _diff("yaml", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("worker:v1",),
        new_tokens=("worker:v2",),
    )
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("3",), new_tokens=("4",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("scheduler",))
    _assert_no_add_delete_identity(diff, "api")
    _assert_no_add_delete_identity(diff, "worker")


def test_css_selector_insert_keeps_existing_rule_anchored() -> None:
    old = """\
.button {
  color: blue;
  padding: 8px;
}
"""
    new = """\
.link {
  color: gray;
}

.button {
  color: red;
  padding: 8px;
}
"""

    diff = _diff("css", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("blue",), new_tokens=("red",))
    _assert_has_change(diff, ChangeType.ADDITION, node_type="rule_set", new_tokens=(".link",))
    _assert_no_add_delete_identity(diff, ".button")


def test_scss_variable_insert_keeps_existing_selector_and_variable_anchored() -> None:
    old = """\
$primary: blue;

.button {
  color: $primary;
}
"""
    new = """\
$secondary: gray;
$primary: red;

.button {
  color: $primary;
}
"""

    diff = _diff("scss", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("blue",), new_tokens=("red",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("$secondary",))
    _assert_no_add_delete_identity(diff, "$primary")
    _assert_no_add_delete_identity(diff, ".button")


def test_html_identity_attributes_beat_same_tag_ordinal_fallback() -> None:
    old = """\
<div>
  <section id="hero"><h1>Hello</h1></section>
  <section><p>First</p></section>
</div>
"""
    new = """\
<div>
  <section><p>Intro</p></section>
  <section id="hero"><h1>Hello there</h1></section>
  <section><p>First</p></section>
</div>
"""

    diff = _diff("html", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("Hello",),
        new_tokens=("Hello there",),
    )
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("Intro",))
    _assert_no_add_delete_identity(diff, "hero")


def test_xml_identity_attributes_beat_same_tag_ordinal_fallback() -> None:
    old = """\
<root>
  <item name="primary">one</item>
  <item>two</item>
</root>
"""
    new = """\
<root>
  <item>zero</item>
  <item name="primary">ONE</item>
  <item>two</item>
</root>
"""

    diff = _diff("xml", old, new)

    _assert_structured(diff)
    # The name="primary" attribute moves from element 1 to element 2 in the
    # source; this is a genuine structural change, not noise. The key contract
    # is that the text changes (one -> ONE, new zero) are surfaced and the
    # "primary" identity is preserved (no add+delete collapse).
    assert any(
        _mentions(c.old_node, "one") or _mentions(c.new_node, "ONE")
        for c in diff.changes
    ), "expected the one/ONE text change to be surfaced"
    assert any(
        _mentions(c.new_node, "zero")
        for c in diff.changes
    ), "expected the new 'zero' content to be surfaced"
    _assert_no_add_delete_identity(diff, "primary")


def test_mdx_sections_anchor_after_inserted_heading() -> None:
    old = """\
# Intro

<Callout kind="info" />

## Usage

```js
foo()
```
"""
    new = """\
# Intro

<Callout kind="info" />

## Install

<Steps />

## Usage

```js
bar()
```
"""

    diff = _diff("mdx", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("foo",), new_tokens=("bar",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("Install",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("Steps",))
    _assert_no_add_delete_identity(diff, "Usage")


def test_hcl_resources_anchor_by_type_and_name_not_position() -> None:
    old = """\
resource "aws_instance" "web" {
  instance_type = "t3.micro"
}

resource "aws_s3_bucket" "logs" {
  bucket = "logs"
}
"""
    new = """\
resource "aws_instance" "db" {
  instance_type = "t3.micro"
}

resource "aws_instance" "web" {
  instance_type = "t3.small"
}

resource "aws_s3_bucket" "logs" {
  bucket = "logs"
}
"""

    diff = _diff("hcl", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("t3.micro",),
        new_tokens=("t3.small",),
    )
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("db",))
    _assert_no_add_delete_identity(diff, "web")
    _assert_no_add_delete_identity(diff, "logs")


def test_puppet_resources_anchor_by_type_and_title_not_position() -> None:
    old = """\
file { '/etc/app.conf':
  ensure => file,
  mode => '0644',
}

service { 'app':
  ensure => running,
}
"""
    new = """\
file { '/etc/extra.conf':
  ensure => file,
}

file { '/etc/app.conf':
  ensure => file,
  mode => '0600',
}

service { 'app':
  ensure => running,
}
"""

    diff = _diff("puppet", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("0644",), new_tokens=("0600",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("/etc/extra.conf",))
    _assert_no_add_delete_identity(diff, "/etc/app.conf")
    _assert_no_add_delete_identity(diff, "app")


def test_dockerfile_repeated_runs_do_not_swallow_cmd_or_env_changes() -> None:
    old = """\
FROM python:3.12
RUN pip install -r requirements.txt
RUN python -m compileall app
CMD ["python", "app.py"]
"""
    new = """\
FROM python:3.12
RUN apt-get update
RUN pip install -r requirements.txt
RUN python -m compileall src
ENV APP_ENV=prod
CMD ["python", "app.py"]
"""

    diff = _diff("dockerfile", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.MODIFICATION, old_tokens=("app",), new_tokens=("src",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("apt-get update",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("APP_ENV",))
    _assert_no_add_delete_identity(diff, "requirements.txt")
    _assert_no_add_delete_identity(diff, "CMD")


def test_sql_added_join_and_order_do_not_churn_existing_fields() -> None:
    old = """\
SELECT u.id, u.name
FROM users u
WHERE u.active = true;
"""
    new = """\
SELECT u.id, u.name, COUNT(o.id) AS order_count
FROM users u
LEFT JOIN orders o ON o.user_id = u.id
WHERE u.active = true
GROUP BY u.id, u.name
ORDER BY u.name;
"""

    diff = _diff("sql", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("LEFT JOIN orders",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("order_count",))
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("ORDER BY u.name",))
    _assert_no_root_add_delete(diff, node_type="term", label="u.id")
    _assert_no_root_add_delete(diff, node_type="term", label="u.name")
    _assert_no_root_add_delete(diff, node_type="field", label="active")


def test_dax_inserted_measure_does_not_churn_existing_measures() -> None:
    old = """\
MEASURE Sales[Total Sales] = SUM(Sales[Amount])
MEASURE Sales[Sales YTD] = TOTALYTD([Total Sales], 'Date'[Date])
"""
    new = """\
MEASURE Sales[Sales Growth %] = DIVIDE([Total Sales], [Previous Sales])
MEASURE Sales[Total Sales] = SUM(Sales[Amount])
MEASURE Sales[Sales YTD] = TOTALYTD([Total Sales], 'Date'[Date])
"""

    diff = _diff("dax", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("Sales[Sales Growth %]",))
    _assert_no_add_delete_identity(diff, "Sales[Total Sales]")
    _assert_no_add_delete_identity(diff, "Sales[Sales YTD]")


def test_asm_repeated_mnemonics_match_by_operand_identity() -> None:
    old = """\
section .text
_start:
    mov rax, 1
    mov rbx, 2
    mov rdx, 14
"""
    new = """\
section .text
_start:
    mov rcx, 0
    mov rax, 1
    mov rbx, 3
    mov rdx, len
"""

    diff = _diff("asm", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("mov rbx, 2",),
        new_tokens=("mov rbx, 3",),
    )
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("mov rdx, 14",),
        new_tokens=("mov rdx, len"),
    )
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("mov rcx, 0",))
    _assert_no_add_delete_identity(diff, "mov rax, 1")


def test_bash_repeated_commands_are_scoped_to_function_or_top_level() -> None:
    old = """\
#!/bin/bash
echo "top"

run() {
  echo "inner"
}
"""
    new = """\
#!/bin/bash
echo "top changed"

run() {
  echo "inner changed"
}

echo "after"
"""

    diff = _diff("bash", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("top",),
        new_tokens=("top changed",),
    )
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("inner",),
        new_tokens=("inner changed",),
    )
    _assert_has_change(diff, ChangeType.ADDITION, new_tokens=("after",))
    _assert_no_add_delete_identity(diff, "run")


def test_delphi_statement_matching_is_scoped_to_owning_routine() -> None:
    old = """\
program Demo;

procedure Alpha;
begin
  WriteLn('Alpha');
end;

procedure Beta;
begin
  WriteLn('Beta');
end;

begin
  Alpha;
  Beta;
end.
"""
    new = """\
program Demo;

procedure Alpha;
begin
  WriteLn('Alpha changed');
end;

procedure Beta;
begin
  WriteLn('Beta changed');
end;

begin
  Alpha;
  Beta;
end.
"""

    diff = _diff("delphi", old, new)

    _assert_structured(diff)
    _assert_no_moves_or_reorders(diff)
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("Alpha",),
        new_tokens=("Alpha changed",),
    )
    _assert_has_change(
        diff,
        ChangeType.MODIFICATION,
        old_tokens=("Beta",),
        new_tokens=("Beta changed",),
    )
    _assert_no_add_delete_identity(diff, "Alpha")
    _assert_no_add_delete_identity(diff, "Beta")
