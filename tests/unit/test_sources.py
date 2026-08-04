"""
tests/unit/test_sources.py — unit tests for Source implementations.
"""

from __future__ import annotations

import textwrap

import pytest

from intentumdiff.sources.string_source import StringSource


class TestStringSource:
    def test_get_content(self):
        src = StringSource("old", "new", "test.py")
        old, new, fname, hint = src.get_content()
        assert old == "old"
        assert new == "new"
        assert fname == "test.py"
        assert hint is None

    def test_with_language_hint(self):
        src = StringSource("a", "b", "x.py", language_hint="python")
        _, _, _, hint = src.get_content()
        assert hint == "python"


class TestPatchSource:
    def test_unified_diff_apply(self):
        from intentumdiff.sources.patch_source import PatchSource

        original = textwrap.dedent("""\
            line1
            line2
            line3
        """)
        patch = textwrap.dedent("""\
            --- a/test.py
            +++ b/test.py
            @@ -1,3 +1,4 @@
             line1
            -line2
            +line2_modified
            +line4
             line3
        """)
        src = PatchSource(patch, original_content=original, filename="test.py")
        old, new, fname, _ = src.get_content()
        assert "line2" in old
        assert "line2_modified" in new
        assert "line4" in new
        assert fname == "test.py"

    def test_filename_extracted_from_patch(self):
        from intentumdiff.sources.patch_source import PatchSource

        patch = textwrap.dedent("""\
            --- a/src/main.py
            +++ b/src/main.py
            @@ -1 +1 @@
            -old
            +new
        """)
        src = PatchSource(patch)
        _, _, fname, _ = src.get_content()
        assert fname == "src/main.py"


class TestFileSource:
    def test_reads_two_files(self, tmp_path):
        from intentumdiff.sources.file_source import FileSource

        f1 = tmp_path / "old.py"
        f2 = tmp_path / "new.py"
        f1.write_text("old_content")
        f2.write_text("new_content")
        src = FileSource(f1, f2)
        old, new, _, _ = src.get_content()
        assert old == "old_content"
        assert new == "new_content"
