"""Optional browser smoke tests for the playground CSP and API integration."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from types import ModuleType

import pytest

from intentdiff.core.models import (  # noqa: E402
    DetectionResult,
    LanguageInfoGroup,
    LanguagePluginInfo,
    SemanticDiff,
)

pytestmark = pytest.mark.browser

CORE_PLUGIN_ID = "intentdiff:python:python"
ALT_PLUGIN_ID = "trusted-extra:python:python"

_BROWSER_DEPS: tuple[ModuleType, ModuleType, ModuleType] | None = None


def _load_browser_deps() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Load optional browser-smoke dependencies at test time.

    Keeping this behind a fixture/test call lets pytest collect the smoke test
    and skip it cleanly when an optional dependency is missing or locally broken.
    """
    global _BROWSER_DEPS
    if _BROWSER_DEPS is None:
        fastapi = pytest.importorskip(
            "fastapi",
            reason="fastapi is unavailable for optional browser smoke tests",
            exc_type=ImportError,
        )
        uvicorn = pytest.importorskip(
            "uvicorn",
            reason="uvicorn is unavailable for optional browser smoke tests",
            exc_type=ImportError,
        )
        playwright_sync = pytest.importorskip(
            "playwright.sync_api",
            reason="playwright is unavailable for optional browser smoke tests",
            exc_type=ImportError,
        )
        _BROWSER_DEPS = (fastapi, uvicorn, playwright_sync)
    return _BROWSER_DEPS


class _BrowserDiffer:
    def __init__(self) -> None:
        self.examples: list[tuple[str, str | None]] = []
        self.detections: list[dict[str, object]] = []
        self.diffs: list[dict[str, object]] = []

    def supported_languages(self) -> list[str]:
        return ["python"]

    def language_info(self) -> list[LanguageInfoGroup]:
        return [
            LanguageInfoGroup(
                language="python",
                selected_plugin_id=CORE_PLUGIN_ID,
                plugins=[
                    LanguagePluginInfo(
                        language_id="python",
                        language_name="Python",
                        language_short_name="Python",
                        monaco_language="python",
                        default_filename="code.py",
                        language_file_extensions=[".py"],
                        author="Core",
                        plugin_version="1.0.0",
                        last_updated="2026-05-19",
                        plugin_id=CORE_PLUGIN_ID,
                        grammar_id="python",
                        priority=0,
                        is_trusted=True,
                        provenance="IntentDiff 1.0.0",
                    ),
                    LanguagePluginInfo(
                        language_id="python",
                        language_name="Python Alt",
                        language_short_name="Py Alt",
                        monaco_language="python",
                        default_filename="alt.py",
                        language_file_extensions=[".py"],
                        author="Alt",
                        plugin_version="1.0.0",
                        last_updated="2026-05-19",
                        plugin_id=ALT_PLUGIN_ID,
                        grammar_id="python",
                        priority=10,
                        is_trusted=True,
                        provenance="trusted-extra 1.0.0",
                    ),
                ],
            )
        ]

    def playground_example(self, language: str, plugin_id: str | None = None) -> dict[str, str]:
        self.examples.append((language, plugin_id))
        if plugin_id == ALT_PLUGIN_ID:
            return {"old": "old alt", "new": "new alt"}
        return {"old": "old core", "new": "new core"}

    def detect_all(
        self,
        content: str,
        candidates: list[str] | None = None,
        preferred_plugins: dict[str, str] | None = None,
        plugin_id: str | None = None,
    ) -> list[DetectionResult]:
        self.detections.append(
            {
                "content": content,
                "candidates": candidates,
                "preferred_plugins": preferred_plugins,
                "plugin_id": plugin_id,
            }
        )
        return [DetectionResult(language="python", grammar_id="python", confidence=1.0)]

    def diff_strings(
        self,
        old: str,
        new: str,
        filename: str,
        language: str | None,
        *,
        parser_plugin_id: str | None = None,
    ) -> SemanticDiff:
        self.diffs.append(
            {
                "old": old,
                "new": new,
                "filename": filename,
                "language": language,
                "parser_plugin_id": parser_plugin_id,
            }
        )
        return SemanticDiff(
            old_filename=filename,
            new_filename=filename,
            language=language or "python",
            changes=[],
            has_semantic_changes=False,
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def playground_server() -> Iterator[tuple[str, _BrowserDiffer]]:
    _, uvicorn, _ = _load_browser_deps()
    from intentdiff.serve import create_app

    differ = _BrowserDiffer()
    app = create_app(differ=differ)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("playground test server did not start")

    try:
        yield url, differ
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_playground_csp_and_api_integration(playground_server: tuple[str, _BrowserDiffer]) -> None:
    url, differ = playground_server
    _, _, playwright_sync = _load_browser_deps()
    error_type = playwright_sync.Error
    expect = playwright_sync.expect
    sync_playwright = playwright_sync.sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except error_type as exc:
            pytest.skip(f"Playwright Chromium browser is not installed: {exc}")

        page = browser.new_page()
        console_messages: list[str] = []
        page_errors: list[str] = []
        page.on("console", lambda msg: console_messages.append(msg.text))
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.route(
            "**/static/vendor/min/vs/loader.js",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body="",
            ),
        )

        page.goto(url, wait_until="networkidle")

        expect(page.locator("#lang-select")).to_have_value("python")
        expect(page.locator("#plugin-select")).to_be_visible()
        page.select_option("#plugin-select", ALT_PLUGIN_ID)
        expect(page.locator("#old-wrap textarea")).to_have_value("old alt")
        expect(page.locator("#new-wrap textarea")).to_have_value("new alt")

        stored = page.evaluate("localStorage.getItem('intentdiff.preferredPlugins.v1')")
        assert json.loads(stored) == {"python": ALT_PLUGIN_ID}
        assert ("python", ALT_PLUGIN_ID) in differ.examples

        page.locator("#compare-btn").click()
        expect(page.locator(".summary")).to_contain_text("No changes")
        assert differ.diffs[-1]["parser_plugin_id"] == ALT_PLUGIN_ID
        assert differ.diffs[-1]["filename"] == "alt.py"

        page.select_option("#lang-select", "")
        page.locator("#new-wrap textarea").fill("def detected():\n    return 1")
        expect(page.locator("#detected-lang")).to_be_visible()
        expect(page.locator("#detected-lang-name")).to_have_text("python")
        assert differ.detections[-1]["preferred_plugins"] == {"python": ALT_PLUGIN_ID}

        browser.close()

    assert not any("Content Security Policy" in msg for msg in console_messages)
    assert page_errors == []
