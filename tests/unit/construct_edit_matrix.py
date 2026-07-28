"""'What happens when I change x' — tree-driven construct-edit matrix (issue #42/#45).

The engine's own semantic tree tells us where every construct lives; this module derives
EDITS from that tree mechanically and checks the diff answers with the right CLASS of
change. No per-language authoring: every language and construct comes from the corpus.

Edit verbs and their expectation classes:

- ``delete_entity``   — remove a named entity's lines → a change whose OLD side mentions the
                        entity; never style-only; the entity must not appear as ADDITION.
- ``rename_entity``   — whole-word rename of an entity's name → a PAIRED change (both sides
                        present) mentioning old on the old side and new on the new side; the
                        old name must not survive as a bare DELETION-only story.
- ``duplicate_entity``— re-insert a renamed copy of the entity after itself → an ADDITION
                        mentioning the copy; no DELETION mentioning the original.
- ``modify_literal``  — change one literal's value → a PAIRED change carrying old→new; never
                        style-only (the issue #41/#46 class).
- ``swap_siblings``   — swap two adjacent same-type named entities → content preserved: no
                        DELETION/ADDITION mentioning either label (MOVE/REORDER/nothing ok).

Ratcheting: ``edit_matrix.expect.json`` per corpus dir records per-verb status written by
the generator: {"ok"} pins the verb green; {"xfail": reason} documents a live gap (fails
loudly when it starts passing); {"skip": reason} = construct not present / edit not
derivable. Regenerate with scripts via tests.unit.construct_edit_matrix:build_manifest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from intentdiff import SemanticDiffer

_ENTITY_HINTS = (
    "function",
    "method",
    "procedure",
    "class",
    "declproc",
    "defproc",
    "sub",
    "form",
    "resource_declaration",
    "markdown_section",
    "rule_set",
    "operation",
)

_LITERAL_HINTS = ("string", "literal", "integer", "float", "number", "char", "scalar")

# Exact literal node types (markup text/content): element text is a value the review
# must surface; the path-profile enrichment synthesises these children (issue #57).
_LITERAL_EXACT = frozenset({"text", "chardata", "content", "code_content", "property_value"})

# Language-SCOPED vocab: node types that are named entities / literal values in one
# language's tree but too generic to hint globally (ocaml `value` bindings would collide
# with yaml value scalars; po `msgstr` is a translation VALUE only in po catalogs).
_ENTITY_EXACT_BY_LANGUAGE = {
    "adf": frozenset({"pipeline", "activity"}),
    "asciidoc": frozenset({"section_level_1", "section_level_2", "section_level_3"}),
    "asm": frozenset({"label"}),
    "databricks": frozenset({"job", "task"}),
    "databricks-workflow": frozenset({"job", "task"}),
    "dax": frozenset({"measure"}),
    "gomod": frozenset({"require_spec", "replace_spec", "module_directive"}),
    "cmake": frozenset({"function_def", "macro_def", "normal_command"}),
    "m": frozenset({"step"}),
    "proto": frozenset({"message", "service", "rpc", "field", "enum"}),
    "make": frozenset({"rule", "variable_assignment"}),
    "ocaml": frozenset({"value"}),
    "po": frozenset({"message"}),
    "reasonml": frozenset({"value"}),
    "sas": frozenset({"data_step", "proc_step"}),
    "sql": frozenset({"create_view", "create_table", "create_function"}),
    "dockerfile": frozenset(
        {
            "from_instruction", "run_instruction", "copy_instruction", "env_instruction",
            "expose_instruction", "workdir_instruction", "label_instruction",
            "healthcheck_instruction", "user_instruction", "cmd_instruction",
        }
    ),
    "elixir": frozenset({"call"}),
    "clojure": frozenset({"list_lit"}),
    "wast": frozenset({"func"}),
    "wat": frozenset({"func"}),
}
_LITERAL_EXACT_BY_LANGUAGE = {
    "asciidoc": frozenset({"paragraph"}),
    "clojure": frozenset({"str_lit", "num_lit"}),
    "ini": frozenset({"setting_value"}),
    "make": frozenset({"recipe_line"}),
    "proto": frozenset({"field_number"}),
    "gomod": frozenset({"version"}),
    "po": frozenset({"msgstr"}),
}

# Scoped entity types that only count at TOP LEVEL (direct children of the root):
# clojure's list_lit matches every nested form — deleting an inner body list breaks
# the enclosing defn into a parse error and the derivation fabricates a token-diff.
_ENTITY_TOP_LEVEL_ONLY = frozenset({"clojure"})

# Exact node-type entities (NOT substring hints — "block" or "pair" as substrings would
# swallow statement_block / every keyed pair-alike everywhere). Markup elements, mdx
# components/sections, hcl blocks, and json/yaml pairs are named review entities in the
# language profiles; the matrix derives the same edits from them (issue #56/#65 burn-down).
_ENTITY_EXACT = frozenset(
    {
        "block",
        "block_mapping_pair",
        "element",
        "flow_pair",
        "jsx_component",
        "pair",
        "section",
    }
)

# Node types (per grammar) whose end position is column 0 of the line AFTER the true
# span — the verified newline-inclusive convention (see _entity_lines).
_NEWLINE_INCLUSIVE_SPAN_TYPES = frozenset(
    {
        "module_directive", "require_spec", "replace_spec", "exclude_spec", "retract_spec",
        # tree-sitter-make uses the same next-line-col-0 end convention.
        "rule", "variable_assignment",
    }
)

_WORD = r"[A-Za-z_][A-Za-z0-9_]*"


@dataclass
class Construct:
    node_type: str
    label: str
    start_line: int
    end_line: int
    end_col: int = -1  # -1 = unknown; 0 marks a newline-inclusive span (see _entity_lines)


def _tree_roots(differ: SemanticDiffer, language: str, filename: str, source: str):
    diff = differ.diff_strings("", source, filename=filename, language_hint=language)
    return [c.new_node for c in diff.changes if c.new_node is not None]


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def detected_line_base(constructs: list[Construct], lines: list[str]) -> int:
    """Detect a parser's line base by label containment.

    Every parser emits tree-sitter's 0-based rows (issue #52 standardized the
    eight 1-based crates), so this is no longer used to ADAPT spans — it backs
    the conformance test that keeps the convention uniform.
    """
    for base in (0, 1):
        hits = 0
        for construct in constructs[:5]:
            idx = construct.start_line - base
            if 0 <= idx < len(lines):
                name = construct.label.split("(")[0].strip().strip("#").strip()
                if name and name.split()[-1] in lines[idx]:
                    hits += 1
        if hits >= max(1, min(2, len(constructs))):
            return base
    return 0


def find_entities(differ, language, filename, source) -> tuple[list[Construct], int]:
    lines = source.splitlines()
    constructs: list[Construct] = []
    exact_constructs: list[Construct] = []
    top_level_only = language.lower() in _ENTITY_TOP_LEVEL_ONLY
    for root in _tree_roots(differ, language, filename, source):
        top_level_ids = {child.id for child in root.children}
        for node in _walk(root):
            node_type = node.node_type.lower()
            hinted = any(h in node_type for h in _ENTITY_HINTS)
            exact = node_type in _ENTITY_EXACT or node_type in _ENTITY_EXACT_BY_LANGUAGE.get(
                language.lower(), frozenset()
            )
            if exact and top_level_only and node.id not in top_level_ids and node.id != root.id:
                exact = False
            label = node.label
            language_scoped = node_type in _ENTITY_EXACT_BY_LANGUAGE.get(
                language.lower(), frozenset()
            )
            if language_scoped and (not label or label == node.node_type):
                # A LANGUAGE-scoped container whose label is its own type (sql
                # create_view) is named by its first labeled descendant (the view
                # name); the container SPAN stays, so deletions/swaps move the whole
                # statement. Global exact types (block/pair/element) keep the strict
                # label rule — naming squirrel's function-body `block`s from their
                # first statement turned bodies into deletable "entities" and broke
                # the ratcheted delete derivation.
                for descendant in _walk(node):
                    if descendant.label and descendant.label != descendant.node_type:
                        label = descendant.label
                        break
            if (hinted or exact) and label and label != node.node_type:
                bucket = constructs if hinted else exact_constructs
                bucket.append(
                    Construct(
                        node.node_type,
                        label,
                        node.position.start_line,
                        node.position.end_line,
                        node.position.end_col,
                    )
                )
    # Exact-type entities (elements/pairs/blocks) are a FALLBACK for languages with no
    # function-like entities; putting them first shifted vue's ratcheted derivations
    # onto template elements.
    constructs = constructs + exact_constructs
    # Degenerate positions (issue #52 family): some parsers (adf, databricks) emit NO
    # node positions — every entity reports lines 0-0 in a multi-line file. Span-based
    # derivations (delete/duplicate/swap) would mutate the wrong lines and fabricate
    # evidence; invalidate the spans so those verbs skip honestly (rename derives from
    # names alone and still works).
    if (
        len(lines) > 3
        and constructs
        and all(c.start_line == 0 and c.end_line == 0 for c in constructs)
    ):
        constructs = [
            Construct(c.node_type, c.label, -1, -1) for c in constructs
        ]
    # Uniform 0-based rows across all parsers (issue #52); the conformance test
    # test_position_convention.py fails if a parser drifts back to 1-based.
    return constructs, 0


def find_literals(differ, language, filename, source) -> list[Construct]:
    literals: list[Construct] = []
    for root in _tree_roots(differ, language, filename, source):
        for node in _walk(root):
            node_type = node.node_type.lower()
            if (
                not node.children
                and (
                    any(h in node_type for h in _LITERAL_HINTS)
                    or node_type in _LITERAL_EXACT
                    or node_type in _LITERAL_EXACT_BY_LANGUAGE.get(language.lower(), frozenset())
                )
                and node.label
                and node.label != node.node_type
                and source.count(node.label) == 1
                and len(node.label.strip()) >= 2
            ):
                literals.append(
                    Construct(node.node_type, node.label, node.position.start_line, node.position.end_line)
                )
    return literals


def _entity_name(construct: Construct) -> str | None:
    match = re.search(_WORD, construct.label)
    return match.group(0) if match else None


def _entity_lines(construct: Construct, base: int, lines: list[str]) -> tuple[int, int] | None:
    start = construct.start_line - base
    end = construct.end_line - base
    # Newline-inclusive spans: gomod's grammar ends nodes at column 0 of the NEXT line —
    # trimming gives the true span; without it every adjacent sibling "overlaps" and
    # swap/delete never derive. SCOPED to verified grammars: qsharp/postscript also
    # report end_col=0 but their spans EXCLUDE their own closing token (a #52 position-
    # convention bug the inclusive interpretation happens to compensate for) — trimming
    # them deletes bodies while orphaning closing braces.
    if construct.end_col == 0 and end > start and construct.node_type in _NEWLINE_INCLUSIVE_SPAN_TYPES:
        end -= 1
    if 0 <= start <= end < len(lines):
        return start, end
    return None


# ---------------------------------------------------------------------------
# Edit derivations: each returns (mutated_source, context) or None if not derivable.
# ---------------------------------------------------------------------------

def _walk_order_then_smallest(entities):
    """Walk-order candidates first (ratchets were generated against walk order), then
    the same entities smallest-span-first as a second pass — whole-file containers
    stop blocking derivation without shifting any ratcheted language. A revisited
    entity derives identically, so the duplicate pass costs nothing but a retry."""
    by_span = sorted(entities, key=lambda e: e.end_line - e.start_line)
    return [*entities, *by_span]


def derive_delete_entity(source, entities, base):
    lines = source.splitlines()
    # Smallest span first: deleting the outermost container (an xml document's root
    # element) leaves orphan close tags and an empty tree; the least destructive
    # candidate that removes every mention of its name is the honest deletion.
    entities = sorted(entities, key=lambda e: e.end_line - e.start_line)
    for entity in entities:
        span = _entity_lines(entity, base, lines)
        name = _entity_name(entity)
        if span is None or name is None or span[0] == 0 and span[1] >= len(lines) - 1:
            continue
        remaining = lines[: span[0]] + lines[span[1] + 1 :]
        mutated = "\n".join(remaining) + "\n"
        if name not in mutated:
            return mutated, {"name": name}
    return None


def derive_rename_entity(source, entities, base):
    # Walk order FIRST (the ratchets were generated against it — resorting shifted
    # delphi/groovy/java/ruby onto different entities), then smallest-span candidates as
    # a fallback for whole-file-container languages. No case-insensitive fallback:
    # parsers that case-normalize labels (SAS uppercases `work.greeting`, and proc
    # labels lead with keywords like PRINT) made it rename KEYWORDS — a derivation
    # artifact, not engine evidence; case-mismatched labels skip honestly (#52 family).
    entities = _walk_order_then_smallest(entities)
    for entity in entities:
        name = _entity_name(entity)
        if not name or len(name) < 3 or name not in source:
            continue
        flags = 0
        renamed = re.sub(rf"\b{re.escape(name)}\b", name + "Renamed", source, flags=flags)
        if renamed != source and name + "Renamed" in renamed:
            return renamed, {"old": name, "new": name + "Renamed"}
    return None


def derive_duplicate_entity(source, entities, base):
    lines = source.splitlines()
    # Walk order first (ratchet-preserving), then smallest-span fallback: duplicating
    # the whole-file container (a databricks job) copies the entire document.
    entities = _walk_order_then_smallest(entities)
    for entity in entities:
        span = _entity_lines(entity, base, lines)
        name = _entity_name(entity)
        if span is None or name is None:
            continue
        block = lines[span[0] : span[1] + 1]
        if not any(name in line for line in block):
            continue
        flags = 0
        copy = [
            re.sub(rf"\b{re.escape(name)}\b", name + "Copy", line, flags=flags)
            for line in block
        ]
        if all(name + "Copy" not in line for line in copy):
            continue
        mutated_lines = lines[: span[1] + 1] + [""] + copy + lines[span[1] + 1 :]
        return "\n".join(mutated_lines) + "\n", {"copy": name + "Copy", "original": name}
    return None


def derive_modify_literal(source, literals):
    for literal in literals:
        old = literal.label
        stripped = old.strip()
        if re.fullmatch(r"\d+", stripped):
            new = str(int(stripped) + 7)
        elif re.fullmatch(r"\d+\.\d+", stripped):
            whole, frac = stripped.split(".", 1)
            new = f"{int(whole) + 7}.{frac}"
        elif (
            stripped[:1] in {"'", '"'}
            or literal.node_type.lower().startswith("string")
            or literal.node_type.lower() in _LITERAL_EXACT
            or any(
                literal.node_type.lower() in types
                for types in _LITERAL_EXACT_BY_LANGUAGE.values()
            )
        ):
            core = old.strip("'\"")
            if not core:
                continue
            new = old.replace(core, core + "X", 1)
        else:
            # Unquoted non-numeric labels (bare words) cannot be mutated safely without
            # risking invalid syntax (a float once fell through here and became 3.14159X).
            continue
        if new != old and source.count(old) == 1:
            return source.replace(old, new, 1), {"old": old, "new": new}
    return None


def derive_swap_siblings(source, entities, base):
    lines = source.splitlines()
    by_type: dict[str, list[Construct]] = {}
    for entity in entities:
        by_type.setdefault(entity.node_type, []).append(entity)
    for group in by_type.values():
        group = sorted(group, key=lambda c: c.start_line)
        for first, second in zip(group, group[1:]):
            span1 = _entity_lines(first, base, lines)
            span2 = _entity_lines(second, base, lines)
            if span1 is None or span2 is None or span1[1] >= span2[0]:
                continue
            gap = lines[span1[1] + 1 : span2[0]]
            block1 = lines[span1[0] : span1[1] + 1]
            block2 = lines[span2[0] : span2[1] + 1]
            mutated_lines = (
                lines[: span1[0]] + block2 + gap + block1 + lines[span2[1] + 1 :]
            )
            name1, name2 = _entity_name(first), _entity_name(second)
            if not name1 or not name2 or name1 == name2:
                continue
            return "\n".join(mutated_lines) + "\n", {"first": name1, "second": name2}
    return None


# ---------------------------------------------------------------------------
# Expectation classes.
# ---------------------------------------------------------------------------

def _mentions(node, token: str) -> bool:
    if node is None:
        return False
    return any(token in n.label for n in _walk(node) if n.label)


def check_delete_entity(diff, ctx) -> str | None:
    if diff.is_style_only:
        return "style-only for a deleted entity"
    if not diff.changes:
        return "zero changes for a deleted entity"
    if not any(_mentions(c.old_node, ctx["name"]) for c in diff.changes):
        return f"no change mentions deleted entity {ctx['name']!r} on the old side"
    return None


def check_rename_entity(diff, ctx) -> str | None:
    if diff.is_style_only:
        return "style-only for a renamed entity"
    paired = [
        c for c in diff.changes
        if c.old_node is not None and c.new_node is not None
        and _mentions(c.old_node, ctx["old"]) and _mentions(c.new_node, ctx["new"])
    ]
    if not paired:
        return f"no paired change carries {ctx['old']!r} -> {ctx['new']!r}"
    return None


def check_duplicate_entity(diff, ctx) -> str | None:
    if diff.is_style_only:
        return "style-only for a duplicated entity"
    if not any(
        c.change_type.value == "ADDITION" and _mentions(c.new_node, ctx["copy"])
        for c in diff.changes
    ):
        return f"no ADDITION mentions the copy {ctx['copy']!r}"
    if any(
        c.change_type.value == "DELETION" and _mentions(c.old_node, ctx["original"])
        for c in diff.changes
    ):
        return f"original {ctx['original']!r} falsely deleted"
    return None


def check_modify_literal(diff, ctx) -> str | None:
    if diff.is_style_only:
        return "style-only for a literal value change (issue #41/#46 class)"
    if not diff.changes:
        return "zero changes for a literal value change"
    paired = [
        c for c in diff.changes
        if c.old_node is not None and c.new_node is not None
    ]
    if not any(_mentions(c.old_node, ctx["old"].strip("'\"")) or _mentions(c.old_node, ctx["old"]) for c in paired):
        return f"no paired change carries the old literal {ctx['old']!r}"
    return None


def check_swap_siblings(diff, ctx) -> str | None:
    for c in diff.changes:
        if c.change_type.value == "DELETION" and c.new_node is None and (
            _mentions(c.old_node, ctx["first"]) or _mentions(c.old_node, ctx["second"])
        ):
            return f"swap decomposed into DELETION of {ctx!r}"
        if c.change_type.value == "ADDITION" and c.old_node is None and (
            _mentions(c.new_node, ctx["first"]) or _mentions(c.new_node, ctx["second"])
        ):
            return f"swap decomposed into ADDITION of {ctx!r}"
    return None


EDIT_VERBS = {
    "delete_entity": (derive_delete_entity, check_delete_entity, "entities"),
    "rename_entity": (derive_rename_entity, check_rename_entity, "entities"),
    "duplicate_entity": (derive_duplicate_entity, check_duplicate_entity, "entities"),
    "modify_literal": (derive_modify_literal, check_modify_literal, "literals"),
    "swap_siblings": (derive_swap_siblings, check_swap_siblings, "entities"),
}


def run_verb(differ, language, filename, source, verb) -> tuple[str, str | None]:
    """Return (status, detail): status in {ok, fail, skip}.

    Tolerance boundary (skip-audit, 2026-07-28): DISCOVERY + DERIVATION on the exotic
    snippet may skip on an exception (matrix sparsity — a parser gap on the unmutated
    source). The MUTATION DIFF + VERIFICATION must never swallow a raise: an engine
    exception there is a product bug and propagates to fail the suite loudly.
    """
    derive, check, needs = EDIT_VERBS[verb]
    try:
        entities, base = find_entities(differ, language, filename, source)
        literals = find_literals(differ, language, filename, source)
        pool = entities if needs == "entities" else literals
        if not pool:
            return "skip", f"no {needs} discoverable in the semantic tree"
        derived = derive(source, pool, base) if needs == "entities" else derive(source, pool)
        if derived is None:
            return "skip", f"{verb} not derivable from this snippet"
        mutated, ctx = derived
    except Exception as exc:  # noqa: BLE001 - derivation must survive exotic snippets
        return "skip", f"raised during mutation derivation: {exc}"
    # Engine + verification phase — no exception tolerance past this point.
    diff = differ.diff_strings(source, mutated, filename=filename, language_hint=language)
    problem = check(diff, ctx)
    if problem is None:
        return "ok", None
    return "fail", problem


def build_manifest(differ, language, filename, source) -> dict:
    verbs: dict[str, dict] = {}
    for verb in EDIT_VERBS:
        status, detail = run_verb(differ, language, filename, source, verb)
        if status == "ok":
            verbs[verb] = {"ok": True}
        elif status == "skip":
            verbs[verb] = {"skip": detail}
        else:
            verbs[verb] = {"xfail": detail}
    return verbs
