"""The LSP Rust source-judged range must use its exact compared Git snapshot."""
from unittest.mock import patch

from intentumdiff.sources.live_buffer_source import LiveBufferSource


def test_lsp_snapshot_is_stable_after_the_first_git_read(tmp_path):
    src = LiveBufferSource(tmp_path, "src.py", "été\n", cache_snapshot=True)
    with patch.object(src, "_read_committed", side_effect=["🦣\n", "changed"] ) as read:
        assert src.get_content()[:2] == ("🦣\n", "été\n")
        assert src.get_content()[:2] == ("🦣\n", "été\n")
        assert read.call_count == 1


def test_default_source_still_reads_fresh_git_content(tmp_path):
    src = LiveBufferSource(tmp_path, "src.py", "x = 2\n")
    with patch.object(src, "_read_committed", side_effect=["x = 1\n", "x = 0\n"]) as read:
        assert src.get_content()[0] == "x = 1\n"
        assert src.get_content()[0] == "x = 0\n"
        assert read.call_count == 2
