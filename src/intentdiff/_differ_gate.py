"""Rust-engine certification gate helpers for intentdiff.differ."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

STRICT_RUST_GATE_ENV = "INTENTDIFF_ENFORCE_RUST_ONLY_ENGINE"


def _validate_git_ref(ref: str) -> None:
    """Reject a git ref that could be mis-read as a ``git`` command-line option
    (argument injection — e.g. ``--output=<path>`` turns ``git diff <ref>`` into
    an arbitrary file write) or inject extra ``cat-file --batch`` request lines.

    A ref starting with ``-`` is invalid per git's own ref-format rules, so this
    never rejects a legitimate ref. The empty ref (working tree) is allowed.
    This guards the public ``diff_commit`` boundary; the Rust native path and the
    GitPython path both consume refs downstream. Security review: issue #88.
    """
    if not ref:
        return
    if ref.startswith("-"):
        raise ValueError(
            f"Invalid git ref {ref!r}: must not start with '-' "
            "(argument-injection guard)."
        )
    if any(ch in ref for ch in ("\n", "\r", "\0")):
        raise ValueError(
            f"Invalid git ref {ref!r}: must not contain a newline or NUL."
        )


# Languages whose single-file live diffs are served by the CERTIFIED Rust batch path.
# This set grows one ported slice at a time (migration plan Phase 2, issue #40) — adding a
# language here asserts the Rust core reproduces its full acceptance contract.
# INTENTDIFF_FORCE_RUST_CERTIFIED (comma-separated language ids) force-adds languages during
# port development: combined with INTENTDIFF_ENFORCE_RUST_ONLY_ENGINE=1, the raised gate
# errors for a forced language are that port's concrete checklist.
RUST_CERTIFIED_LANGUAGES: frozenset[str] = frozenset({"python"})


def _rust_certified_languages() -> frozenset[str]:
    forced = os.environ.get("INTENTDIFF_FORCE_RUST_CERTIFIED", "")
    if not forced:
        return RUST_CERTIFIED_LANGUAGES
    return RUST_CERTIFIED_LANGUAGES | {
        part.strip().lower() for part in forced.split(",") if part.strip()
    }


# Languages whose per-stage draft FINALIZATION routes through the Rust core (issue #57 —
# the retirement path for python refinement/presentation). Grows per parity-verified
# language; INTENTDIFF_RUST_FINALIZE_LANGS (comma-separated) adds pilots.
RUST_FINALIZE_LANGUAGES: frozenset[str] = frozenset(
    {
        "go", "kotlin", "ruby", "swift", "java", "c", "cpp", "gitignore",
        "csharp", "elixir", "perl", "scala", "xml", "haskell",
        # Structured-language batch (issue #57): parity-verified clean under routing —
        # no engine change, per-language contracts + cross-cutting infra all green.
        "adf", "asciidoc", "assemblyscript", "clojure", "databricks",
        "databricks-workflow", "dax", "groovy", "latex", "lua", "ocaml", "odin",
        "php", "plsql", "po", "postscript", "qsharp", "reasonml",
        "sas", "squirrel", "tsql", "vbnet", "wast", "wat",
        "puppet", "hcl",
        # Body-reference variable renames corroborated by an anchored callable's param rename
        # (promote_corroborated_variable_renames) — the routed analogue of refactoring.py's
        # inferred_rename_pairs, which the default path used for dart's single-letter renames.
        "dart",
        # Structured/markup batch (#57): parity-verified clean under routing (full-suite gated).
        # generic was piloted too but breaks the generic-text/markdown/text fallback path
        # (#35/#36/#44) — deferred to its own fix.
        "svelte", "astro", "vue", "mdx", "scss",
        # dockerfile: needs the resource-profile shell-command identity keying (dockerfile_key),
        # now ported to Rust so an inserted RUN doesn't cross-pair positionally.
        "dockerfile",
        # asm (experimental, #57): statement-profile language — instructions key by mnemonic +
        # first-operand identity (asm_key) so an operand-value edit is a MODIFICATION, not churn.
        "asm",
        # css (#57): unblocked by applying semantic invariances in the routed short-circuit —
        # the color-canonicalization equivalence (red≡#ff0000, blue≡#0000ff) now fires under
        # routing. (yaml/json graceful-xfail shape + python dedent still gap-blocked.)
        "css",
        # bash (#57): bash_key ported (commands key by NAME) + the cross-key MODIFICATION split —
        # `:` -> `echo Hello` unpaires into DELETE+ADD with descendant scaffold folded.
        "bash",
        # delphi (#57): delphi_key ported (statements keyed by call/assign identity) + statement
        # review containers anchor in suppress_parent_modifications — the WriteLn edit presents
        # as ONE statement MODIFICATION with descendant mods folded.
        "delphi",
        # m / Power Query (#57): served by the dax parser (dax routed-green); the step-edit
        # contract reformulated to anti-swallowing (routed pairs the predicate edit as ONE
        # MODIFICATION carrying both sides — honest, not the default's DELETE+ADD split).
        "m",
        # powershell (#57/#66): unblocked by the entity-anchored matching port — reordered
        # functions pair by identity and surface as MOVEs (31/31 force-routed).
        "powershell",
        # json/yaml (#57): keyed-data profile port — pairs/items key by ancestor key path; array
        # scalars/items by CONTENT identity (not position labels), so an inserted element never
        # cross-pairs later unchanged values; identical-content positional churn suppresses with
        # evidence; yaml block↔flow representation equivalence (YAML 1.2 §3) is style, not change.
        "json", "yaml",
        # html (#57/#64): path-profile keying port — elements key by their element PATH with
        # identity attributes (id/name/key/…) beating same-tag ordinals, and relocated
        # identical-content elements keep their match (stable-same-label escape) — a text edit
        # inside an id-bearing or restructured element survives sibling inserts.
        "html",
        # rust (#57/#60): unlocked by the entity-anchored matching port (augment_entity_matching)
        # — match→if-let now surfaces as a statement-rewrite MODIFICATION instead of losing the
        # relocated arm bodies to DELETE+ADD churn. Full rust suite 112/112 force-routed.
        "rust",
        # abap (#57): entity-anchored grouping ported (entity_child_content_groups) — a child
        # modification under a same-identity form groups as "GREET changed", not a free leaf edit.
        "abap",
        # r (#57): unblocked by the any→all truthiness fix in the signature-coverage suppression —
        # a NEW function sharing param labels with a rename is no longer erased.
        "r",
        # zig (#57): parity-verified clean under routing post-anchors (14/14 force-routed) —
        # pure routing, no engine change.
        "zig",
        # sql (#57): query-profile port (sql_key + matching/changes augmentation) — clauses,
        # relations, and fields pair by role + normalized identity within their statement, so an
        # added JOIN doesn't shift FROM into a bogus MOVE and unchanged SELECT fields never churn.
        # The dax half of the profile is deliberately NOT ported (dax routed green without it).
        "sql",
        # freebasic/terraform (#57): parity-verified clean under routing (dedicated tests +
        # the cross-cutting suites in the full gate) — pure routing, no engine change.
        "freebasic", "terraform",
        # graphql (#57/#67): unblocked by porting surface_changed_in_place_entity — a matched
        # named entity (type/query/fragment) whose body changed carries its label into a group.
        "graphql",
        # javascript/typescript/tsx (#57): three ports close the js-family gap — (1) the
        # function-valued-declaration profile (const f = () => … pairs with function f(…) as
        # one entity) in the anchors code, (2) the style rule relabelling suppression residue
        # as an IGNORED_STYLE group (provenance "suppression"), and (3) moved-pair literal
        # update recovery (the #37 leaf score, move-pair scoped): edit-script pruning of
        # unmatched leaves under moved subtrees was swallowing md5→sha256 inside a hoisted
        # calc_hash. Same-label DELETE+ADD container churn around reported renames suppresses
        # under a rename-coverage truthiness guard (tighter than the python pass it ports).
        "javascript", "typescript", "tsx",
        # generic (#57): the text review and both markdown section passes were already
        # Rust-first; the routed short-circuit now mirrors the stage-12 orchestration
        # (token churn replaced wholesale by Rust line spans, markdown section passes for
        # .md filenames, stage-12 style-only resolution for empty results).
        "generic",
        # python (#57): the flagship, last. The certified BATCH path (sources->final in
        # Rust, semantic_contract rust_finalized_v1) still serves default configs FIRST;
        # the tier behind it is the full-Rust 9-fin routing (the stage-11 hybrid and the
        # transitional python stages were deleted in the stage-4b payoff).
        "python",
        # markdown (#44): the tree-sitter markdown parser takes .md off the generic
        # fallback — sections/headings/fences are REAL nodes (the corpus "section"
        # entity vocabulary and the reorder→MOVE promotion consume them natively).
        "markdown",
        # toml (#48): keyed config format — tables/pairs are labeled by header/key
        # identity in the parser itself, so the routed finalize pairs value edits
        # under stable keys natively.
        "toml",
        # ini (#48): keyed config — sections by header, settings by key, same identity
        # model as toml; certified on the same template.
        "ini",
        # gomod (#48): keyed dependency manifest — specs by module path, versions as
        # semantic children; a version bump is ONE modification under stable identity.
        "gomod",
        # make (#48): rules by target identity, assignments by variable name, recipe
        # lines as semantic children — a recipe edit pairs under its target.
        "make",
        # proto (#48): messages/services/enums/rpcs by name, fields by identifier with
        # type + field number as semantic children — the wire-compat review story.
        "proto",
        # cmake (#48): commands labeled identifier(first-arg) so target identity is
        # part of the command; function/macro defs by their defined name.
        "cmake",
    }
)


def _rust_finalize_languages() -> frozenset[str]:
    # The set is TOTAL (all 67 supported languages, issue #57 2026-07-15), so it now
    # serves as the certified-language allowlist. The INTENTDIFF_RUST_FINALIZE_LANGS
    # pilot escape hatch is retired with the campaign: it existed to force-route
    # candidates during per-language certification, and every candidate is routed.
    # New languages certify by joining RUST_FINALIZE_LANGUAGES in code, behind the
    # same protocol (force-routed probe -> Rust pin -> full gate -> flip).
    return RUST_FINALIZE_LANGUAGES


def _strict_rust_only_enabled() -> bool:
    return os.getenv(STRICT_RUST_GATE_ENV, "").strip() == "1"


class RustOnlyGateError(RuntimeError):
    """Raised when the RUST_ONLY gate blocks a genuine token-level fallback.

    Subclasses ``RuntimeError`` so existing ``pytest.raises(RuntimeError, …)``
    assertions still match, but its distinct type lets the commit-path fan-out
    (``diff_commit``'s per-file executor) tell a fatal gate trip apart from the
    ordinary per-file ``RuntimeError`` it deliberately swallows-and-skips. A
    swallowed gate error would silently drop a file instead of failing loudly,
    defeating the whole point of the gate — so the executor re-raises this type.
    """


def _raise_rust_only_gate_error(reason: str | None) -> None:
    if reason is not None and _strict_rust_only_enabled():
        raise RustOnlyGateError(f"Rust-only engine gate prevented fallback: {reason}")


