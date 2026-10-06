"""Real Git routing must retain recognized source formats, through the Rust C ABI."""
import subprocess

import pytest

from intentumdiff.sources.git_source import iter_changed_sources


@pytest.mark.parametrize("filename,old,new", [
    ("code.sh", "#!/bin/bash\necho one\n", "#!/bin/bash\necho two\n"),
    ("index.html", "<!DOCTYPE html><html><title>One</title></html>", "<!DOCTYPE html><html><title>Two</title></html>"),
    ("data.xml", '<?xml version="1.0"?><root>One</root>', '<?xml version="1.0"?><root>Two</root>'),
    ("Component.svelte", "<script>let n = 1;</script><h1>{n}</h1>", "<script>let n = 2;</script><h1>{n}</h1>"),
    ("module.wat", "(module (func (result i32) i32.const 1))", "(module (func (result i32) i32.const 2))"),
    ("module.wast", "(module (func (result i32) i32.const 1))", "(module (func (result i32) i32.const 2))"),
    ("code.ps", "%!PS\n(One) show\nshowpage\n", "%!PS\n(Two) show\nshowpage\n"),
])
def test_recognized_source_survives_working_tree_and_commit_review(tmp_path, filename, old, new):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.STDOUT)

    git("init")
    git("config", "core.autocrlf", "false")
    source = tmp_path / filename
    # Keep fixture bytes identical across platforms; do not translate LF on Windows.
    source.write_bytes(old.encode("utf-8"))
    git("add", filename)
    git("-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid", "commit", "-m", "baseline")
    baseline = git("rev-parse", "HEAD").decode().strip()
    source.write_bytes(new.encode("utf-8"))
    working = list(iter_changed_sources(tmp_path, baseline))
    assert [(a, b, c, d) for a, b, c, d, _ in working] == [(old, new, filename, filename)]
    git("add", filename)
    git("-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid", "commit", "-m", "change")
    committed = list(iter_changed_sources(tmp_path, baseline, "HEAD"))
    assert [(a, b, c, d) for a, b, c, d, _ in committed] == [(old, new, filename, filename)]
