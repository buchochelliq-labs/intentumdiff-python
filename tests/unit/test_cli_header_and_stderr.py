"""The first thing a user sees, and the noise printed beside it.

Both defects covered here produced *correct diffs*. The classification was right, the
change count was right, the exit code was 0 — and the tool still looked broken, because
the header said it had compared a file with itself and stderr carried a warning naming a
"allow vulnerable" override.

That is why these assertions exist separately from the diff tests: a passing diff test
was never going to catch either one.
"""

from __future__ import annotations

import logging

from intentumdiff import SemanticDiffer
from intentumdiff.sources.file_source import FileSource
from intentumdiff.sources.string_source import StringSource

OLD = "def greet(name):\n    return 'hi ' + name\n"
NEW = "def greet(name):\n    if not name:\n        return None\n    return 'hi ' + name\n"


def _files(tmp_path):
    old = tmp_path / "a.py"
    new = tmp_path / "b.py"
    old.write_text(OLD, encoding="utf-8")
    new.write_text(NEW, encoding="utf-8")
    return old, new


def test_two_files_keep_their_own_names(tmp_path):
    # The reported bug: both sides were labelled with the NEW filename, so the header
    # claimed a file had been diffed against itself.
    old, new = _files(tmp_path)
    diff = SemanticDiffer().diff(FileSource(old, new))
    assert diff.old_filename == "a.py"
    assert diff.new_filename == "b.py"


def test_an_explicit_display_filename_still_wins(tmp_path):
    # FileSource lets a caller override the display name; that must apply to both sides
    # rather than being half-overridden by the fix above.
    old, new = _files(tmp_path)
    diff = SemanticDiffer().diff(FileSource(old, new, filename="renamed.py"))
    assert diff.old_filename == diff.new_filename == "renamed.py"


def test_identical_basenames_are_left_alone(tmp_path):
    # The common case for a git-style comparison: one path, two versions. Nothing should
    # be "corrected" here.
    a = tmp_path / "one" / "mod.py"
    b = tmp_path / "two" / "mod.py"
    for p, text in ((a, OLD), (b, NEW)):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    diff = SemanticDiffer().diff(FileSource(a, b))
    assert diff.old_filename == diff.new_filename == "mod.py"


def test_a_non_git_diff_claims_no_staging_scope():
    # The header used to print "Scope: working tree" unconditionally, which is simply
    # untrue for a diff of two in-memory strings.
    diff = SemanticDiffer().diff(StringSource(OLD, NEW, "example.py"))
    assert diff.staging_status is None


def test_a_successful_diff_logs_nothing_at_warning_or_above(caplog, tmp_path, monkeypatch):
    # 0.0.1 printed ~69 plugin errors on every run while returning correct results, and
    # the exit code called that success. This asserts the quiet the user should get.
    # Assert what a USER gets. The suite sets the allow-vulnerable override, whose
    # warning is legitimate precisely because it is deliberate.
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_VULNERABLE_WASMTIME", raising=False)
    old, new = _files(tmp_path)
    with caplog.at_level(logging.WARNING):
        diff = SemanticDiffer().diff(FileSource(old, new))

    assert diff.changes, "fixture should produce at least one change"
    noisy = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not noisy, "a successful diff must be silent, got: " + "; ".join(
        f"{r.levelname} {r.getMessage()[:120]}" for r in noisy
    )
