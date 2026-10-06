"""The human CLI uses Rust presentation; required failures never become blank success."""
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from intentumdiff import rust_core
from intentumdiff.cli import _shared
from intentumdiff.core.models import SemanticDiff


def sample():
    return SemanticDiff(old_filename="before.py", new_filename="after.py", language="python")


def test_transport_preserves_dto_and_terminal_options(monkeypatch):
    requests = []
    def render(request):
        requests.append(json.loads(request))
        return json.dumps("Rust-rendered panel\n")
    monkeypatch.setattr(rust_core, '_load_backend', lambda: SimpleNamespace(render_cli_review=render))
    assert rust_core.render_cli_review(sample(), width=80, color=False) == "Rust-rendered panel\n"
    assert requests == [{"diff": sample().model_dump(mode="json"), "width": 80, "color": False}]


def test_required_renderer_failure_propagates(monkeypatch):
    monkeypatch.setattr(rust_core, '_load_backend', lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match='render_cli_review'):
        rust_core.render_cli_review(sample(), width=80, color=False)


def test_terminal_writes_rust_text_verbatim_and_honors_no_color(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(_shared, '_console', Console(file=stream, force_terminal=True, width=48, height=24))
    monkeypatch.setenv('NO_COLOR', '1')
    requests = []
    def render(diff, **options):
        requests.append(options)
        return 'literal [red] label\n'
    monkeypatch.setattr(rust_core, 'render_cli_review', render)
    _shared._render_terminal(sample())
    assert stream.getvalue() == 'literal [red] label\n'
    assert requests == [{"width": 48, "color": False}]


@pytest.mark.parametrize("legacy_windows,expected_color", [(True, False), (False, True)])
def test_direct_output_uses_ansi_only_on_supported_console(monkeypatch, legacy_windows, expected_color):
    stream = io.StringIO()
    # Legacy consoles are terminals and report a colour system, but cannot
    # consume ANSI written directly without Rich's Win32 translation.
    console = Console(file=stream, force_terminal=True, color_system="windows" if legacy_windows else "standard",
                      legacy_windows=legacy_windows, width=80, height=24)
    monkeypatch.setattr(_shared, '_console', console)
    monkeypatch.delenv('NO_COLOR', raising=False)
    options = []
    def render(diff, **kwargs):
        options.append(kwargs)
        return '\x1b[31mchange\x1b[0m\n' if kwargs['color'] else 'change\n'
    monkeypatch.setattr(rust_core, 'render_cli_review', render)
    _shared._render_terminal(sample())
    assert options == [{"width": console.width, "color": expected_color}]
    assert ('\x1b[' in stream.getvalue()) is expected_color
