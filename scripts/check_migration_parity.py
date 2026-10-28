"""Check actual native Rust/public Python output against shared source expectations.

Run after building core's migration_probe example. Evidence output is optional.
Only route-specific metadata, tree IDs/hashes and audit-only NOISE_SUPPRESSED
counts are excluded: those describe execution, not source changes.
"""
from __future__ import annotations
import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import tempfile
from contextlib import closing
from intentumdiff import SemanticDiffer, review_text
from intentumdiff.core.models import DiffConfig


def projection(diff):
    def node(value):
        if value is None:
            return None
        # Preserve the full semantic subtree. Comparing only the change root
        # can accept a native route that drops descendants or corrupts their
        # positions while Python still returns the complete changed tree.
        return {
            **{k: value[k] for k in ('node_type', 'label', 'position')},
            'children': [node(child) for child in (value.get('children') or [])],
        }
    return {
        'changes': [{'change_type': c['change_type'], 'old_node': node(c.get('old_node')),
                     'new_node': node(c.get('new_node')), 'refactoring_kind': c.get('refactoring_kind')}
                    for c in diff['changes']],
        'groups': [{'kind': g['kind'], 'raw_change_indices': g.get('raw_change_indices', [])}
                   for g in diff['change_groups'] if g['kind'] != 'NOISE_SUPPRESSED'],
        'has_semantic_changes': diff['has_semantic_changes'],
        'is_style_only': diff['is_style_only'], 'is_fallback': diff['is_fallback'],
    }


def check_expectations(case, diff):
    changes = diff['changes']
    labels = [c[side]['label'] for c in changes for side in ('old_node', 'new_node') if c.get(side)]
    for label in case.get('required_labels', []):
        assert label in labels, (case['id'], label, diff)
    if 'exact_changes' in case:
        assert len(changes) == case['exact_changes'], (case['id'], diff)
    if 'end_columns' in case:
        assert [changes[0][side]['position']['end_col'] for side in ('old_node', 'new_node')] == case['end_columns'], (case['id'], diff)
    if 'moves' in case:
        assert sum(c['change_type'] == 'MOVE' for c in changes) == case['moves'], (case['id'], diff)
    for group in diff['change_groups']:
        assert group['kind'] not in case.get('forbidden_group_kinds', []), (case['id'], diff)
    if case.get('forbid_ignored_style'):
        assert not diff['metadata'].get('ignored_style_changes'), (case['id'], diff)
    if 'expected' in case:
        expected = case['expected']
        assert len(changes) == len(expected['changes']), diff
        for actual, wanted in zip(changes, expected['changes']):
            assert actual['change_type'] == wanted['change_type'], diff
            for side in ('old', 'new'):
                node = actual[side + '_node']
                assert node['label'] == wanted[side + '_label'], diff
                assert [node['position']['start_line'], node['position']['start_col']] == wanted[side + '_start'], diff
        assert not any(c['change_type'] in expected['forbidden_change_types'] for c in changes), diff
    assert diff['has_semantic_changes'] and not diff['is_style_only'], (case['id'], diff)




def corpus_mutations(source: str, manifest: dict) -> list[tuple[str, str]]:
    """Deterministic source edits matching the existing corpus unit-test fixture.

    This generates inputs only; all judgments remain independently source-based.
    """
    import re

    mutations: list[tuple[str, str]] = []
    bumped = re.sub(
        r"(?<![\w.])(\d+)(?![\w.])", lambda match: str(int(match.group(1)) + 1),
        source, count=1,
    )
    if bumped != source:
        mutations.append(("bump_numeric_literal", bumped))
    for quote in ("'", '"'):
        pattern = re.compile(rf"{quote}([^{quote}\n]{{2,}}){quote}")
        edited = pattern.sub(lambda match: f"{quote}{match.group(1)}X{quote}", source, count=1)
        if edited != source:
            mutations.append((f"edit_{quote}_string", edited))
            break
    append = manifest.get("append_statement")
    if append:
        mutations.append(("append_statement", source.rstrip("\n") + "\n" + append + "\n"))
    return mutations


_RELEASE_CORPUS_LANGUAGES = frozenset("""
    abap adf asciidoc asm assemblyscript astro bash c clojure cmake cpp csharp css dart
    databricks-workflow databricks dax delphi dockerfile elixir generic gitignore go
    gomod graphql groovy haskell hcl html ini java javascript json kotlin latex lua m
    make markdown mdx ocaml odin perl php plsql po postscript powershell proto puppet
    python qsharp r reasonml ruby rust sas scala scss sql squirrel svelte swift toml
    tsql tsx typescript vbnet vue wast wat xml yaml zig
""".split())

def full_corpus_cases() -> list[dict]:
    """Require every packaged corpus language and independently meaningful source edits.

    This is not a Python semantic oracle: the edits come from corpus source and
    each native/Python result must pass source-derived expectations separately.
    """
    root = Path(__file__).resolve().parents[1] / "tests/fixtures/corpus"
    assert root.is_dir(), "74-language corpus directory is missing"
    directories = sorted(p for p in root.iterdir() if p.is_dir())
    actual_languages = {directory.name for directory in directories}
    assert actual_languages == _RELEASE_CORPUS_LANGUAGES, (
        f"missing: {sorted(_RELEASE_CORPUS_LANGUAGES - actual_languages)}; "
        f"unexpected: {sorted(actual_languages - _RELEASE_CORPUS_LANGUAGES)}"
    )
    cases = []
    for language_dir in directories:
        manifest_path = language_dir / "playground.expect.json"
        assert manifest_path.is_file(), f"missing {manifest_path}"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        language = manifest["language"]
        assert language == language_dir.name, (language_dir, language)
        assert manifest.get("filename"), manifest_path
        assert not manifest.get("mutation_xfail"), (
            f"{language}: no 0.0.2 content-mutation exemptions permitted"
        )
        sources = [
            path for path in language_dir.glob("playground.*")
            if not path.name.endswith(".expect.json")
        ]
        assert len(sources) == 1, f"expected exactly one playground source for {language}"
        source = sources[0].read_text(encoding="utf-8")
        assert source.strip(), f"empty corpus source for {language}"
        cases.append({
            "id": f"corpus-{language}-addition",
            "old": "",
            "new": source,
            "filename": manifest["filename"],
            "mode": "sources",
            "corpus_language": language,
            "forbid_ignored_style": True,
            "required_visible_tokens": list(manifest.get("visible_tokens") or []),
        })
        for mutation_name, edited in corpus_mutations(source, manifest):
            assert edited != source, (language, mutation_name)
            cases.append({
                "id": f"corpus-{language}-{mutation_name}",
                "old": source,
                "new": edited,
                "filename": manifest["filename"],
                "mode": "sources",
                "corpus_language": language,
            })
    assert {case["corpus_language"] for case in cases} == {
        directory.name for directory in directories
    }
    return cases


def check_visible_corpus_tokens(case: dict, diff: dict) -> None:
    """Validate manifest-ratcheted semantic content independently for each API.

    Agreement alone can hide shared parser regressions. Inspect the entire
    changed subtree and require all previously visible source tokens.
    """
    expected = case.get("required_visible_tokens")
    if expected is None:
        return
    labels: list[str] = []
    for change in diff["changes"]:
        root = change.get("new_node")
        if root is None:
            continue
        stack = [root]
        while stack:
            node = stack.pop()
            if node.get("label"):
                labels.append(node["label"])
            stack.extend(node.get("children") or [])
    visible = "\n".join(labels)
    missing = [token for token in expected if token not in visible]
    assert not missing, (
        case["id"], "missing source-ratcheted semantic tokens", missing,
        "labels seen", visible[:2000],
    )


def check_available_corpus_parser(case: dict, *, system: str | None = None, machine: str | None = None) -> None:
    """Fail closed for deliberately unavailable native components, never accept generic.

    The Rust C ABI owns platform exclusions. The Windows/ARM64 PowerShell Wasm
    crashes Cranelift during compilation; this remains an explicit RC blocker,
    not a silently skipped language or a successful generic fallback.
    """
    from intentumdiff.rust_core import _c_abi_call

    system = system if system is not None else platform.system()
    machine = machine if machine is not None else platform.machine()
    status = _c_abi_call("parser_availability", case["corpus_language"], case["filename"], system, machine)
    assert isinstance(status, dict) and "reason" in status and "unavailable" in status, (
        case["id"], "malformed Rust parser availability contract", status,
    )
    assert status["reason"] is None and status["unavailable"] is None, (
        case["id"], f"semantic parser unavailable on {system}/{machine}", status,
        "0.0.2 release blocker: https://github.com/buchochelliq-labs/intentumdiff-python/issues/42",
    )


def check_source_spans(case: dict, diff: dict) -> None:
    """Check every exposed changed-tree span against actual UTF-8 source bytes.

    Equality across Rust and Python alone is insufficient: both could agree on
    the same invalid offsets. This catches non-source-grounded synthetic nodes.
    """
    for change in diff["changes"]:
        for side, source_key in (("old_node", "old"), ("new_node", "new")):
            node = change.get(side)
            if node is None:
                continue
            lines = case[source_key].splitlines(keepends=True)
            stack = [node]
            while stack:
                current = stack.pop()
                position = current["position"]
                start = (position["start_line"], position["start_col"])
                end = (position["end_line"], position["end_col"])
                assert start <= end, (case["id"], side, current.get("label"), position)
                for line_number, column in (start, end):
                    assert type(line_number) is int and type(column) is int, (
                        case["id"], side, position
                    )
                    assert 0 <= line_number <= len(lines), (
                        case["id"], side, position, len(lines)
                    )
                    limit = (
                        len(lines[line_number].encode("utf-8"))
                        if line_number < len(lines) else 0
                    )
                    assert 0 <= column <= limit, (
                        case["id"], side, current.get("label"), position,
                        f"line {line_number} max UTF-8 byte column {limit}"
                    )
                stack.extend(current.get("children") or [])


def check_vbnet_declaration_extent(case: dict, diff: dict) -> None:
    """Source-judge the full VB.NET declaration span, including End Function.

    The previous pinned Wasm used the last body child's end line with the
    opening declaration's byte width (e.g. row 10, column 60 for a 21-byte
    line). Merely bounding column values is weaker than checking this
    exact fixture against the actual closing source line.
    """
    if case["id"] != "corpus-vbnet-addition":
        return
    lines = case["new"].splitlines()
    opening = next(i for i, line in enumerate(lines)
                   if line.strip().startswith("Function Multiply("))
    closing = next(i for i in range(opening + 1, len(lines))
                   if lines[i].strip().lower() == "end function")
    start_col = len(lines[opening].encode("utf-8")) - len(lines[opening].lstrip().encode("utf-8"))
    expected = (opening, start_col, closing, len(lines[closing].encode("utf-8")))
    found = []
    for change in diff["changes"]:
        root = change.get("new_node")
        if root is None:
            continue
        stack = [root]
        while stack:
            node = stack.pop()
            if node.get("label") == "Multiply" and node.get("node_type") == "function_statement":
                pos = node["position"]
                found.append(tuple(pos[k] for k in
                                   ("start_line", "start_col", "end_line", "end_col")))
            stack.extend(node.get("children") or [])
    assert found and set(found) == {expected}, (
        case["id"], "VB.NET Multiply full declaration span", expected, found
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--core-dir', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--wasm-dir', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    core, native, wasm = args.core_dir.resolve(), args.native.resolve(), args.wasm_dir.resolve()
    cases = json.loads((core / 'tests/fixtures/migration/markdown.json').read_text(encoding='utf-8'))
    cases = [dict(c, filename='doc.md', mode='text') for c in cases]
    schema = json.loads((core / 'tests/corpus/schema_custom_identity.json').read_text(encoding='utf-8'))
    cases.append(dict(schema, id='schema-custom-identity', mode='sources'))
    corpus_cases = full_corpus_cases()
    cases.extend(corpus_cases)
    # full_corpus_cases independently enforces the exact 74-language inventory.
    records = []
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        schemas = root / '.intentumdiff/schemas'
        schemas.mkdir(parents=True)
        (schemas / 'acme.json').write_text(json.dumps(schema['descriptor']), encoding='utf-8')
        os.chdir(root)
        try:
            with closing(SemanticDiffer(DiffConfig(strict_plugins=True))) as differ:
                for case in cases:
                    request = dict(case, repo=str(root), wasm=str(wasm))
                    if 'corpus_language' in case:
                        check_available_corpus_parser(case)
                        # Extensions such as .yml and .json are intentionally shared by
                        # multiple parsers (e.g. Databricks Workflow/YAML and ADF/JSON).
                        # Pin the corpus language in BOTH native and Python requests:
                        # filename-only selection can silently exercise the wrong parser.
                        request['handler'] = 'native_selection_diff'
                        request['config'] = {'language_hint': case['corpus_language'], 'strict_plugins': True}
                    result = subprocess.run([str(native)], input=json.dumps(request), text=True, encoding='utf-8', capture_output=True, check=True)
                    native_result = json.loads(result.stdout)
                    if 'corpus_language' in case:
                        assert isinstance(native_result, dict) and 'fallback' not in native_result, (
                            case['id'], 'native parser declined', native_result
                        )
                        assert isinstance(native_result.get('diff'), dict), (
                            case['id'], 'native review response missing diff', native_result
                        )
                        rust = native_result['diff']
                    else:
                        rust = native_result
                    python = (review_text(case['old'], case['new'], filename=case['filename']) if case['mode'] == 'text' else differ.diff_strings(case['old'], case['new'], case['filename'], language_hint=case.get('corpus_language'))).model_dump(mode='json')
                    check_expectations(case, rust)
                    check_expectations(case, python)
                    if 'corpus_language' in case:
                        for origin, output in (('native', rust), ('python', python)):
                            assert output['language'] == case['corpus_language'], (
                                case['id'], origin, output['language'])
                            assert output['changes'], (case['id'], origin, 'zero changes')
                            check_source_spans(case, output)
                            check_visible_corpus_tokens(case, output)
                            check_vbnet_declaration_extent(case, output)
                    assert projection(rust) == projection(python), (case['id'], projection(rust), projection(python))
                    records.append(dict(case, rust=rust, python=python))
        finally:
            os.chdir(original)
    from intentumdiff.rust_core import _c_abi_call
    for case in json.loads((core / 'tests/fixtures/migration/utilities.json').read_text(encoding='utf-8')):
        rust = json.loads(subprocess.run([str(native)], input=json.dumps(case), text=True, encoding='utf-8', capture_output=True, check=True).stdout)
        if case['handler'] == 'infer_file_lifecycle':
            python = _c_abi_call(case['handler'], *case['args'])
        else:
            profile = _c_abi_call('schema_profiles', json.dumps({'operation': 'validate', 'document': {
                'language_id': 'test', 'match': {'filename_patterns': [case['pattern']]}, 'identity_fields': ['key']},
                'path': 'profile.json', 'raw_text': ''}))['profile']
            python = _c_abi_call('schema_profiles', json.dumps({'operation': 'match', 'profiles': [profile],
                'filename': case['filename'], 'content': '{}'})) is not None
        assert rust == python == case['expected'], (case, rust, python)
        records.append(dict(case, rust=rust, python=python))
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(records, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{len(records)} source-judged native Rust/Python cases passed')


if __name__ == '__main__':
    main()
