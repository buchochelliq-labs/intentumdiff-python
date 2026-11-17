"""Core #252/#312 review completeness: marshalled verbatim, shown verbatim, reported on consent.

Core states ``completeness`` (complete | equivalent | incomplete) and, for an incomplete
review, ``incomplete`` with a sanitised bug report. Python only transports these fields
(no re-derivation), and the CLI opens core's pre-filled issue URL only after the user
previews the report and explicitly answers yes.
"""
from __future__ import annotations

import copy
import io
import json
import sys

import pytest

from intentumdiff import SemanticDiffer, rust_core
from intentumdiff.cli import _build_parser, _shared
from intentumdiff.core.models import SemanticDiff

GO_OLD = "package m\nfunc f() {\n\tsecretAlpha()\n\tsecretBeta()\n}\n"
GO_NEW = "package m\nfunc f() {\n\tsecretBeta()\n\tsecretAlpha()\n}\n"

MESSAGE = "IntentumDiff could not explain this change. [canned] Please report a bug."
TITLE = "Incomplete review: unexplained_source_delta (go, core 0.1.0)"
BODY = "### IntentumDiff could not explain a change\n\n- shape: `call_expression[id1](id1)`"
ISSUE_URL = "https://github.com/buchochelliq-labs/intentumdiff-core/issues/new?title=t&body=b"


def canned_incomplete(*, truncated: bool = False) -> dict:
    """A core-shaped incomplete review DTO (the layout of docs/C_ABI.md, core #252)."""
    return {
        "old_filename": "a.go", "new_filename": "a.go", "language": "go",
        "changes": [], "change_groups": [], "parse_errors": [], "guardrail_violations": [],
        "has_semantic_changes": False, "is_style_only": False, "is_fallback": False,
        "llm_summary": "", "gitignore_excluded": False,
        "metadata": {"no_surviving_changes": True},
        "completeness": "incomplete",
        "incomplete": {
            "reason": "unexplained_source_delta", "message": MESSAGE, "report_url": ISSUE_URL,
            "language": "go", "core_version": "0.1.0",
            "source_ranges": [{"old_start_byte": 26, "old_end_byte": 36, "new_start_byte": 26,
                               "new_end_byte": 36, "old_start_line": 2, "old_end_line": 4,
                               "new_start_line": 2, "new_end_line": 4}],
            "source_ranges_truncated": False, "source_ranges_available": True,
            "report": {
                "title": TITLE, "body_markdown": BODY, "truncated": truncated,
                "issue_url": ISSUE_URL,
                "outline": {
                    "schema_version": 1, "reason": "unexplained_source_delta",
                    "core_version": "0.1.0", "language": "go",
                    "plugin": {"id": "go", "version": "sha256:4b60"},
                    "regions": [{"index": 1,
                                 "old": {"start_line": 4, "start_col": 0, "end_line": 4,
                                         "end_col": 0, "bytes": 0},
                                 "new": {"start_line": 4, "start_col": 0, "end_line": 5,
                                         "end_col": 0, "bytes": 13},
                                 "old_skeleton": "source_file > block",
                                 "old_shape": "block(id1)"}],
                    "regions_truncated": False, "structure_available": True,
                    "suppressions": [{"pass": "refine:suppress_descendant_noise",
                                      "removed": [{"change_type": "MOVE",
                                                   "node_type": "call_expression",
                                                   "count": 2}],
                                      "added": []}],
                    "placeholder_count": 2,
                    "future_field": {"kept": True},
                },
            },
        },
    }


def through_python_layer(dto: dict) -> SemanticDiff:
    return SemanticDiff.model_validate(rust_core._require_complete_batch_diff_dto(dto))


# ---------------------------------------------------------------------------
# Marshalling with the real core
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def differ() -> SemanticDiffer:
    return SemanticDiffer()


def test_unchanged_sources_are_complete(differ):
    diff = differ.diff_strings("x = 1\n", "x = 1\n", "a.py")
    assert (diff.completeness, diff.incomplete) == ("complete", None)


def test_a_real_change_is_complete(differ):
    diff = differ.diff_strings("x = 1\n", "x = 2\n", "a.py")
    assert diff.completeness == "complete" and diff.changes


def test_style_only_edit_is_equivalent(differ):
    diff = differ.diff_strings("x=1\n", "x = 1\n", "a.py")
    assert (diff.completeness, diff.incomplete, diff.is_style_only) == ("equivalent", None, True)


@pytest.mark.parametrize("old,new,filename", [
    ('{"a": 1, "b": 2}\n', '{"b": 2, "a": 1}\n', "x.json"),
    ("a: 1\nb: 2\n", "b: 2\na: 1\n", "x.yaml"),
    ("a = 1\nb = 2\n", "b = 2\na = 1\n", "x.toml"),
])
def test_keyed_member_reorder_is_equivalent_with_proof(differ, old, new, filename):
    diff = differ.diff_strings(old, new, filename)
    assert diff.completeness == "equivalent"
    assert diff.incomplete is None
    assert diff.metadata["equivalence_proof"] == "keyed_data.unordered_mapping_member_order"
    assert diff.changes == [] and diff.has_semantic_changes is False


def test_go_call_reorder_is_incomplete_through_real_core(differ):
    diff = differ.diff_strings(GO_OLD, GO_NEW, "m.go")
    assert diff.completeness == "incomplete"
    assert diff.changes == [] and diff.has_semantic_changes is False
    incomplete = diff.incomplete
    assert incomplete.reason == "unexplained_source_delta"
    assert incomplete.source_ranges_available and incomplete.source_ranges
    report = incomplete.report
    assert report.issue_url == incomplete.report_url
    # Python carries the suppression record to core, so the outline names the passes.
    assert report.outline.structure_available and report.outline.suppressions
    for secret in ("secretAlpha", "secretBeta", "m.go"):
        assert secret not in json.dumps(incomplete.model_dump(mode="json"))


def test_incomplete_dto_round_trips_verbatim():
    dto = canned_incomplete()
    diff = through_python_layer(copy.deepcopy(dto))
    dumped = json.loads(diff.model_dump_json())
    assert dumped["completeness"] == "incomplete"
    assert dumped["incomplete"] == dto["incomplete"]  # keys, unknown fields and omissions
    suppression = diff.incomplete.report.outline.suppressions[0]
    assert suppression.pass_ == "refine:suppress_descendant_noise"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("completeness"),
    lambda d: d.__setitem__("completeness", "clean"),
    lambda d: d.pop("incomplete"),
    lambda d: d.__setitem__("incomplete", None),
    lambda d: d.__setitem__("completeness", "complete"),
    lambda d: d["incomplete"].pop("message"),
    lambda d: d["incomplete"].pop("report"),
    lambda d: d["incomplete"].__setitem__("source_ranges_truncated", "false"),
    lambda d: d["incomplete"]["source_ranges"][0].__setitem__("old_start_byte", -1),
    lambda d: d["incomplete"]["report"].__setitem__("truncated", 1),
    lambda d: d["incomplete"]["report"]["outline"].__setitem__("regions", {}),
    lambda d: d["incomplete"]["report"]["outline"]["suppressions"][0].pop("pass"),
])
def test_bad_completeness_shapes_raise_translation_error(mutate):
    dto = canned_incomplete()
    mutate(dto)
    with pytest.raises(ValueError):
        through_python_layer(dto)


def test_finalize_proof_and_suppression_record_are_transported(monkeypatch):
    sent = []
    real = rust_core._c_abi_call

    def spy(op, *args):
        if op == "complete_routed_review":
            sent.append(json.loads(args[0])["finalized"])
        return real(op, *args)

    monkeypatch.setattr(rust_core, "_c_abi_call", spy)
    SemanticDiffer().diff_strings('{"a": 1, "b": 2}\n', '{"b": 2, "a": 1}\n', "x.json")
    assert sent[0]["equivalence_proof"] == "keyed_data.unordered_mapping_member_order"
    assert "suppression_trace" in sent[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool) -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def opened(monkeypatch):
    calls: list[str] = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: calls.append(url) or True)
    return calls


def _go_files(tmp_path):
    old, new = tmp_path / "confidential_name.go", tmp_path / "confidential_name_new.go"
    old.write_text(GO_OLD, encoding="utf-8")
    new.write_text(GO_NEW, encoding="utf-8")
    return str(old), str(new)


def _run_cli(monkeypatch, capsys, argv, *, tty: bool, answer: str = ""):
    monkeypatch.setattr(sys, "stdin", _Stdin(answer, tty))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty, raising=False)
    args = _build_parser().parse_args(argv)
    args.func(args)
    return capsys.readouterr()


def _real_report(old: str, new: str):
    return SemanticDiffer().diff_strings(GO_OLD, GO_NEW, new).incomplete


def test_cli_shows_core_message_not_no_changes(tmp_path, monkeypatch, capsys, opened):
    old, new = _go_files(tmp_path)
    out = _run_cli(monkeypatch, capsys, ["file", old, new], tty=False)
    assert "Incomplete review" in out.out
    assert "No changes detected" not in out.out + out.err
    message = _real_report(old, new).message
    assert message in out.err  # verbatim
    assert _shared._REPORT_HOW_TO in out.err
    assert opened == []


def test_cli_never_opens_without_a_tty_even_with_report_bug(tmp_path, monkeypatch, capsys, opened):
    old, new = _go_files(tmp_path)
    out = _run_cli(monkeypatch, capsys, ["file", old, new, "--report-bug"], tty=False,
                   answer="y\n")
    report = _real_report(old, new).report
    assert report.title in out.err and report.body_markdown in out.err
    assert _shared._REPORT_PROMPT not in out.err
    assert opened == []


@pytest.mark.parametrize("answer", ["", "\n", "n\n", "no\n", "maybe\n"])
def test_cli_does_not_open_without_explicit_yes(tmp_path, monkeypatch, capsys, opened, answer):
    old, new = _go_files(tmp_path)
    out = _run_cli(monkeypatch, capsys, ["file", old, new], tty=True, answer=answer)
    assert _shared._REPORT_PROMPT in out.err
    assert _shared._REPORT_NOT_OPENED in out.err
    assert opened == []


@pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n"])
def test_cli_opens_core_issue_url_on_yes(tmp_path, monkeypatch, capsys, opened, answer):
    old, new = _go_files(tmp_path)
    _run_cli(monkeypatch, capsys, ["file", old, new], tty=True, answer=answer)
    assert opened == [_real_report(old, new).report.issue_url]


def test_cli_preview_contains_only_core_text(tmp_path, monkeypatch, capsys, opened):
    """Privacy: apart from fixed CLI wording, stderr is exactly core's message and report."""
    old, new = _go_files(tmp_path)
    out = _run_cli(monkeypatch, capsys, ["file", old, new], tty=True, answer="n\n")
    incomplete = _real_report(old, new)
    report = incomplete.report
    expected = (f"{incomplete.message}\n{_shared._REPORT_PREVIEW_HEADER}\n{report.title}\n"
                f"{report.body_markdown}\n{_shared._REPORT_PROMPT}{_shared._REPORT_NOT_OPENED}\n")
    assert out.err == expected
    for local in ("confidential_name", str(tmp_path), "secretAlpha", "secretBeta"):
        assert local not in out.err


def test_json_output_passes_fields_through_and_never_prompts(tmp_path, monkeypatch, capsys, opened):
    old, new = _go_files(tmp_path)
    out = _run_cli(monkeypatch, capsys, ["file", old, new, "--json"], tty=True, answer="y\n")
    payload = json.loads(out.out)
    incomplete = _real_report(old, new)
    assert payload["completeness"] == "incomplete"
    assert payload["incomplete"] == json.loads(incomplete.model_dump_json())
    assert _shared._REPORT_PROMPT not in out.err and _shared._REPORT_HINT in out.err
    assert opened == []


@pytest.mark.parametrize("tty", [True, False])
def test_truncated_report_says_url_shortened_and_prints_full_body(monkeypatch, capsys, opened, tty):
    diff = through_python_layer(canned_incomplete(truncated=True))

    class _Differ:
        _config = type("C", (), {"plugin_fuel": None})()

        def diff(self, _source):
            return diff

    from intentumdiff.cli import _commands
    monkeypatch.setattr(_commands, "_differ", lambda *a, **k: _Differ())
    monkeypatch.setattr(_commands, "_render_with_profile",
                        lambda d, args, **k: _shared._offer_incomplete_reports([d], args))
    monkeypatch.setattr(_commands, "_emit_guardrail_reports", lambda *a, **k: None)
    monkeypatch.setattr(_commands, "_exit_if_guardrail_result_fails", lambda *a, **k: None)
    out = _run_cli(monkeypatch, capsys, ["file", "a.go", "b.go", "--report-bug"], tty=tty,
                   answer="y\n")
    assert BODY in out.err and TITLE in out.err and MESSAGE in out.err
    assert _shared._REPORT_TRUNCATED_NOTE in out.err
    assert opened == ([ISSUE_URL] if tty else [])
    # Core's Markdown is written literally, never as Rich markup.
    assert "[canned]" in out.err and "call_expression[id1](id1)" in out.err


def test_watcher_renders_an_incomplete_review_instead_of_no_changes(tmp_path):
    from unittest.mock import MagicMock, patch

    from intentumdiff.watcher import FileWatcher

    diff = through_python_layer(canned_incomplete())
    render_fn = MagicMock()
    watcher = FileWatcher([tmp_path], MagicMock(), render_fn=render_fn)
    lines: list[str] = []
    watcher._out = MagicMock()
    watcher._out.print = lambda msg, **kw: lines.append(str(msg))
    watcher._differ.diff.return_value = diff
    with (patch("intentumdiff.watcher.resolve_repo_root", return_value=str(tmp_path)),
          patch("intentumdiff.watcher.WorkingTreeSource")):
        watcher._run_diff(str(tmp_path / "a.go"))
    assert not any("no changes" in line for line in lines)
    render_fn.assert_called_once_with(diff)


def test_public_review_routes_carry_cores_verdict(tmp_path, differ):
    from intentumdiff import FileSource, review_text

    assert review_text("a\n", "a\n").completeness == "complete"
    old, new = _go_files(tmp_path)
    assert differ.diff(FileSource(old_path=old, new_path=new)).completeness == "incomplete"
