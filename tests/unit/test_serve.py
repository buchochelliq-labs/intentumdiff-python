"""
tests/unit/test_serve.py
~~~~~~~~~~~~~~~~~~~~~~~~

Unit tests for the intentumdiff HTTP playground API (``intentumdiff serve``).

All tests use a mock SemanticDiffer injected via ``create_app(differ=...)``,
so no Wasm plugins or git repositories are required.
"""

from __future__ import annotations

import asyncio
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from intentumdiff.core.models import LanguageInfoGroup, LanguagePluginInfo, SemanticDiff
from intentumdiff.plugins.exceptions import PluginNotFoundError

TestClient: Any
create_app: Any
_MAX_REQUEST_BODY_BYTES: int

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    from fastapi.testclient import TestClient

    from intentumdiff.serve import create_app
    from intentumdiff.serve._app import _MAX_REQUEST_BODY_BYTES

    _SERVE_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:
    TestClient = None
    create_app = None
    _MAX_REQUEST_BODY_BYTES = 0
    _SERVE_IMPORT_ERROR = exc

pytestmark = pytest.mark.skipif(
    _SERVE_IMPORT_ERROR is not None,
    reason=f"FastAPI/httpx serve test dependencies unavailable: {_SERVE_IMPORT_ERROR}",
)


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self.scripts.append({key: value or "" for key, value in attrs})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_diff(**kwargs) -> SemanticDiff:
    defaults: dict = dict(
        old_filename="code.py",
        new_filename="code.py",
        language="python",
        changes=[],
    )
    return SemanticDiff(**(defaults | kwargs))


def _mock_differ(result: SemanticDiff | Exception | None = None) -> MagicMock:
    """Return a MagicMock that acts as a minimal SemanticDiffer."""
    differ = MagicMock()
    differ._registry.parsers = []
    if isinstance(result, Exception):
        differ.diff.side_effect = result
    else:
        differ.diff.return_value = result if result is not None else _make_diff()
    return differ


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestGetIndex:
    def test_returns_html(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "playground" in resp.text.lower()

    def test_csp_disallows_inline_scripts(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.get("/")

        assert "script-src 'self'" in resp.headers["content-security-policy"]
        script_src = resp.headers["content-security-policy"].split("script-src", 1)[1]
        assert "'unsafe-inline'" not in script_src.split(";", 1)[0]

    def test_static_html_uses_only_self_hosted_external_scripts(self):
        html_path = (
            Path(__file__).parents[2]
            / "src"
            / "intentumdiff"
            / "serve"
            / "_static"
            / "index.html"
        )
        parser = _ScriptParser()
        parser.feed(html_path.read_text(encoding="utf-8"))

        assert parser.scripts
        assert all(script.get("src") for script in parser.scripts)
        srcs = {script["src"] for script in parser.scripts}
        assert "/static/vendor/min/vs/loader.js" in srcs
        assert "/static/playground.js" in srcs
        assert not any(src.startswith(("http://", "https://", "//")) for src in srcs)

    def test_playground_script_is_self_hosted(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.get("/static/playground.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "'use strict'" in resp.text

    def test_playground_script_uses_api_for_detection_and_examples(self):
        js_path = (
            Path(__file__).parents[2]
            / "src"
            / "intentumdiff"
            / "serve"
            / "_static"
            / "playground.js"
        )
        text = js_path.read_text(encoding="utf-8")

        assert "fetch('/detect'" in text
        assert "/example/" in text
        assert "fetch('/language-info'" in text
        assert "plugin_id=" in text
        assert "preferred_plugins" in text
        assert "formatChangeType" in text
        assert "change-meta" in text
        assert "DETECT_PATTERNS" not in text
        assert "function detectLanguage" not in text
        assert "MONACO_MAP" not in text
        assert "LANG_FILENAME" not in text

    def test_playground_languages_load_without_monaco_vendor(self):
        js_path = (
            Path(__file__).parents[2]
            / "src"
            / "intentumdiff"
            / "serve"
            / "_static"
            / "playground.js"
        )
        text = js_path.read_text(encoding="utf-8")

        assert "function populateLanguageSelector()" in text
        assert text.index("populateLanguageSelector().then(startEditors)") > text.index(
            "function startEditors()"
        )
        assert "typeof require === 'function'" in text
        assert "Monaco loader unavailable" in text
        assert "createFallbackEditor" in text
        assert "fallback-editor" in text


# ---------------------------------------------------------------------------
# GET /languages
# ---------------------------------------------------------------------------


class TestGetLanguages:
    def test_empty_registry_returns_empty_list(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.get("/languages")
        assert resp.status_code == 200
        assert resp.json() == {"languages": []}

    def test_uses_public_supported_languages_api(self):
        differ = MagicMock()
        differ.supported_languages.return_value = ["sql", "python", "sql"]

        client = TestClient(create_app(differ=differ))
        resp = client.get("/languages")

        assert resp.status_code == 200
        assert resp.json() == {"languages": ["python", "sql"]}
        differ.supported_languages.assert_called_once_with()

    def test_aggregates_all_parser_language_ids(self):
        differ = _mock_differ()
        adapter_a = MagicMock()
        adapter_a.language_ids = ["python", "py"]
        adapter_b = MagicMock()
        adapter_b.language_ids = ["javascript", "js"]
        differ._registry.parsers = [adapter_a, adapter_b]

        client = TestClient(create_app(differ=differ))
        resp = client.get("/languages")
        assert resp.status_code == 200
        langs = resp.json()["languages"]
        assert "python" in langs
        assert "py" in langs
        assert "javascript" in langs
        assert langs == sorted(langs), "languages must be sorted"


class TestGetLanguageInfo:
    def test_uses_public_language_info_api(self):
        differ = MagicMock()
        differ.language_info.return_value = [
            LanguageInfoGroup(
                language="python",
                selected_plugin_id="core:python:python",
                plugins=[
                    LanguagePluginInfo(
                        language_id="python",
                        language_name="Python",
                        language_short_name="Python",
                        monaco_language="python",
                        default_filename="code.py",
                        language_file_extensions=[".py"],
                        author="Core",
                        plugin_version="0.1.0",
                        last_updated="2026-05-19",
                        plugin_id="core:python:python",
                        grammar_id="python",
                        priority=0,
                        is_trusted=True,
                        provenance="IntentumDiff 0.0.1 beta",
                    )
                ],
            )
        ]

        client = TestClient(create_app(differ=differ))
        resp = client.get("/language-info")

        assert resp.status_code == 200
        body = resp.json()
        assert body["languages"][0]["language"] == "python"
        assert body["languages"][0]["selectedPluginId"] == "core:python:python"
        plugin = body["languages"][0]["plugins"][0]
        assert plugin["pluginId"] == "core:python:python"
        assert plugin["languageName"] == "Python"
        assert plugin["monacoLanguage"] == "python"
        assert plugin["defaultFilename"] == "code.py"
        assert plugin["isTrusted"] is True
        differ.language_info.assert_called_once_with()

    def test_falls_back_to_legacy_languages_for_plain_test_doubles(self):
        differ = _mock_differ()
        adapter = MagicMock()
        adapter.language_ids = ["python"]
        differ._registry.parsers = [adapter]

        client = TestClient(create_app(differ=differ))
        resp = client.get("/language-info")

        assert resp.status_code == 200
        body = resp.json()
        assert body["languages"][0]["language"] == "python"
        assert body["languages"][0]["plugins"][0]["pluginId"] == "python"


# ---------------------------------------------------------------------------
# POST /diff
# ---------------------------------------------------------------------------


class TestPostDiff:
    def test_valid_request_returns_200_with_diff_json(self):
        diff = _make_diff()
        client = TestClient(create_app(differ=_mock_differ(diff)))
        resp = client.post(
            "/diff",
            json={"old": "x = 1", "new": "x = 2", "filename": "code.py"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "language" in body
        assert "changes" in body

    def test_language_hint_accepted(self):
        diff = _make_diff(language="javascript")
        client = TestClient(create_app(differ=_mock_differ(diff)))
        resp = client.post(
            "/diff",
            json={"old": "var a = 1", "new": "let a = 1", "language": "javascript"},
        )
        assert resp.status_code == 200

    def test_plugin_id_forwards_to_diff_strings(self):
        diff = _make_diff(language="python")
        differ = _mock_differ()
        differ.diff_strings.return_value = diff

        client = TestClient(create_app(differ=differ))
        resp = client.post(
            "/diff",
            json={
                "old": "x = 1",
                "new": "x = 2",
                "filename": "code.py",
                "language": "python",
                "plugin_id": "core:python:python",
            },
        )

        assert resp.status_code == 200
        differ.diff.assert_not_called()
        differ.diff_strings.assert_called_once_with(
            "x = 1",
            "x = 2",
            "code.py",
            "python",
            parser_plugin_id="core:python:python",
        )

    def test_extra_json_field_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.post(
            "/diff",
            json={"old": "a", "new": "b", "junk": "x"},
        )
        assert resp.status_code == 422

    def test_path_traversal_in_filename_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.post(
            "/diff",
            json={"old": "a", "new": "b", "filename": "../../etc/passwd"},
        )
        assert resp.status_code == 422

    def test_slash_in_filename_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.post(
            "/diff",
            json={"old": "a", "new": "b", "filename": "src/code.py"},
        )
        assert resp.status_code == 422

    def test_unknown_language_returns_422(self):
        differ = _mock_differ()
        differ.diff.side_effect = PluginNotFoundError("no_such_lang", "code.xyz")
        client = TestClient(create_app(differ=differ))
        resp = client.post(
            "/diff",
            json={"old": "a", "new": "b", "language": "no_such_lang"},
        )
        assert resp.status_code == 422
        assert "Unknown language" in resp.json()["detail"]

    def test_old_content_over_512kb_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        big = "x" * (513 * 1024)
        resp = client.post("/diff", json={"old": big, "new": "b"})
        assert resp.status_code == 422

    def test_new_content_over_512kb_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        big = "x" * (513 * 1024)
        resp = client.post("/diff", json={"old": "a", "new": big})
        assert resp.status_code == 422

    def test_body_limit_without_content_length_rejected(self):
        app = create_app(differ=_mock_differ())
        body = json.dumps({
            "old": "a",
            "new": "b",
            "junk": "x" * (_MAX_REQUEST_BODY_BYTES + 1),
        }).encode("utf-8")
        messages = [
            {"type": "http.request", "body": body, "more_body": False},
        ]
        sent: list[dict] = []

        async def receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/diff",
            "raw_path": b"/diff",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }

        asyncio.run(app(scope, receive, send))

        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 413

    def test_image_sized_json_envelope_reaches_schema_validation(self):
        client = TestClient(create_app(differ=_mock_differ()))
        body = {
            "old": "a",
            "new": "b",
            "image_like_payload": "x" * (1_411 * 1024),
        }

        resp = client.post("/diff", json=body)

        assert resp.status_code == 422
        assert "Request body too large" not in resp.text


# ---------------------------------------------------------------------------
# POST /detect
# ---------------------------------------------------------------------------


class TestPostDetect:
    def test_detect_delegates_to_library(self):
        """POST /detect calls differ.detect_all and returns its result."""
        from intentumdiff.core.models import DetectionResult

        differ = _mock_differ()
        differ.detect_all.return_value = [
            DetectionResult(language="python", grammar_id="python-parser", confidence=1.0),
            DetectionResult(language="ruby", grammar_id="ruby-parser", confidence=0.5),
        ]
        client = TestClient(create_app(differ=differ))
        resp = client.post("/detect", json={"content": "def foo(): pass"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "python"
        assert body["candidates"][0]["language"] == "python"
        assert body["candidates"][1]["language"] == "ruby"

    def test_detect_no_match_returns_null_language(self):
        differ = _mock_differ()
        differ.detect_all.return_value = []
        client = TestClient(create_app(differ=differ))
        resp = client.post("/detect", json={"content": "???"})
        assert resp.status_code == 200
        assert resp.json()["language"] is None
        assert resp.json()["candidates"] == []

    def test_detect_candidates_forwarded_to_library(self):
        from intentumdiff.core.models import DetectionResult

        differ = _mock_differ()
        differ.detect_all.return_value = [
            DetectionResult(language="typescript", grammar_id="ts-parser", confidence=1.0),
        ]
        client = TestClient(create_app(differ=differ))
        resp = client.post("/detect", json={
            "content": "const x: string = 'hi';",
            "candidates": ["typescript", "javascript"],
        })
        assert resp.status_code == 200
        assert resp.json()["language"] == "typescript"
        differ.detect_all.assert_called_once_with(
            "const x: string = 'hi';", ["typescript", "javascript"]
        )

    def test_detect_preferred_plugins_forwarded_to_library(self):
        from intentumdiff.core.models import DetectionResult

        differ = _mock_differ()
        differ.detect_all.return_value = [
            DetectionResult(language="python", grammar_id="python-parser", confidence=1.0),
        ]
        client = TestClient(create_app(differ=differ))
        resp = client.post(
            "/detect",
            json={
                "content": "def f(): pass",
                "preferred_plugins": {"python": "core:python:python"},
            },
        )

        assert resp.status_code == 200
        differ.detect_all.assert_called_once_with(
            "def f(): pass",
            None,
            {"python": "core:python:python"},
        )

    def test_detect_plugin_id_forwards_to_library(self):
        from intentumdiff.core.models import DetectionResult

        differ = _mock_differ()
        differ.detect_all.return_value = [
            DetectionResult(language="python", grammar_id="python-parser", confidence=1.0),
        ]
        client = TestClient(create_app(differ=differ))
        resp = client.post(
            "/detect",
            json={
                "content": "def f(): pass",
                "plugin_id": "core:python:python",
            },
        )

        assert resp.status_code == 200
        differ.detect_all.assert_called_once_with(
            "def f(): pass",
            None,
            None,
            "core:python:python",
        )

    def test_detect_unknown_plugin_id_returns_422(self):
        differ = _mock_differ()
        differ.detect_all.side_effect = PluginNotFoundError("core:python:missing")
        client = TestClient(create_app(differ=differ))
        resp = client.post(
            "/detect",
            json={
                "content": "def f(): pass",
                "plugin_id": "core:python:missing",
            },
        )

        assert resp.status_code == 422

    def test_oversized_content_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        big = "x" * (513 * 1024)
        resp = client.post("/detect", json={"content": big})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# XSS regression: hostile change_type must survive round-trip as plain data
# ---------------------------------------------------------------------------


class TestXssRegressions:
    """Verify that hostile change_type values are not interpreted as markup."""

    HOSTILE = '<img src=x onerror=alert(1)>'

    def test_diff_endpoint_sanitises_or_rejects_hostile_filename(self):
        """POST /diff must either reject hostile input (422) or echo it safely.

        The API must never produce a 500 error for hostile input.
        """
        diff = _make_diff(
            old_filename=self.HOSTILE,
            new_filename=self.HOSTILE,
        )
        differ = _mock_differ(result=diff)
        client = TestClient(create_app(differ=differ))

        resp = client.post(
            "/diff",
            json={
                "old_content": "x=1",
                "new_content": "x=2",
                "old_filename": self.HOSTILE,
                "new_filename": self.HOSTILE,
            },
        )

        # The server must reject (422) or succeed (200).
        # A 500 would mean the hostile input triggered an unhandled error.
        assert resp.status_code in (200, 422), (
            f"Expected 200 or 422 for hostile input, got {resp.status_code}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert body["old_filename"] == self.HOSTILE


# ---------------------------------------------------------------------------
# GET /example/{language} — language identifier validation (F6-6)
# ---------------------------------------------------------------------------


class TestGetExample:
    """GET /example/{language} must validate the language path parameter."""

    def _client(self):
        differ = _mock_differ()
        differ.playground_example.return_value = None
        return TestClient(create_app(differ=differ))

    def test_valid_language_returns_200(self):
        resp = self._client().get("/example/python")
        # playground_example returns None → {"old": null, "new": null}
        assert resp.status_code == 200

    def test_plugin_id_forwards_to_library(self):
        differ = _mock_differ()
        differ.playground_example.return_value = {"old": "a", "new": "b"}
        client = TestClient(create_app(differ=differ))

        resp = client.get("/example/python?plugin_id=core:python:python")

        assert resp.status_code == 200
        assert resp.json() == {"old": "a", "new": "b"}
        differ.playground_example.assert_called_once_with(
            "python",
            "core:python:python",
        )

    def test_invalid_plugin_id_rejected_422(self):
        resp = self._client().get("/example/python?plugin_id=bad/plugin")
        assert resp.status_code == 422

    def test_unknown_plugin_id_rejected_422(self):
        differ = _mock_differ()
        differ.playground_example.side_effect = PluginNotFoundError("missing")
        client = TestClient(create_app(differ=differ))

        resp = client.get("/example/python?plugin_id=core:python:missing")

        assert resp.status_code == 422

    def test_invalid_chars_rejected_422(self):
        resp = self._client().get("/example/python%3Becho")
        assert resp.status_code == 422

    def test_dotdot_traversal_rejected_422(self):
        resp = self._client().get("/example/../secret")
        # FastAPI normalises the URL; the resulting segment should fail
        assert resp.status_code in (404, 422)

    def test_overlong_language_rejected_422(self):
        lang = "a" * 65
        resp = self._client().get(f"/example/{lang}")
        assert resp.status_code == 422

    def test_at_sign_rejected_422(self):
        resp = self._client().get("/example/python@3.12")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /detect — candidates field validation (F6-6)
# ---------------------------------------------------------------------------


class TestDetectCandidatesValidation:
    """DetectRequest.candidates must enforce count and per-item byte limits."""

    def test_too_many_candidates_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.post("/detect", json={
            "content": "x = 1",
            "candidates": [f"lang{i}" for i in range(51)],
        })
        assert resp.status_code == 422

    def test_exactly_50_candidates_accepted(self):
        from intentumdiff.core.models import DetectionResult
        differ = _mock_differ()
        differ.detect_all.return_value = [
            DetectionResult(language="python", grammar_id="python-parser", confidence=1.0),
        ]
        client = TestClient(create_app(differ=differ))
        resp = client.post("/detect", json={
            "content": "x = 1",
            "candidates": [f"lang{i}" for i in range(50)],
        })
        assert resp.status_code == 200

    def test_overlong_candidate_rejected(self):
        client = TestClient(create_app(differ=_mock_differ()))
        resp = client.post("/detect", json={
            "content": "x = 1",
            "candidates": ["a" * 65],
        })
        assert resp.status_code == 422
