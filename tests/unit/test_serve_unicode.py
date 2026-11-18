"""#316: escaped lone surrogates get a stable 4xx JSON response and never reach the engine."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from intentumdiff.core.models import SemanticDiff  # noqa: E402
from intentumdiff.serve import create_app  # noqa: E402

_DIFF_BODY = {"old": "x=1", "new": "x=2", "filename": "f.py"}
_LONE = ["\\ud800", "\\udfff", "a\\ud83d", "\\ude00b"]  # high, low, unpaired at either end


def _client() -> tuple[TestClient, MagicMock]:
    differ = MagicMock()
    differ.diff.return_value = SemanticDiff(
        old_filename="f.py", new_filename="f.py", language="python"
    )
    differ.diff_strings.return_value = differ.diff.return_value
    differ.detect_all.return_value = []
    return TestClient(create_app(differ=differ), raise_server_exceptions=False), differ


def _raw(body: dict, field: str, escaped: str) -> bytes:
    text = json.dumps({**body, field: "__X__"})
    return text.replace('"__X__"', f'"{escaped}"').encode("ascii")


@pytest.mark.parametrize("escaped", _LONE)
@pytest.mark.parametrize("field", ["old", "new", "filename", "language", "plugin_id"])
def test_diff_rejects_escaped_lone_surrogates(field, escaped) -> None:
    client, differ = _client()
    response = client.post(
        "/diff", content=_raw(_DIFF_BODY, field, escaped),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "surrogate" in response.json()["detail"]
    differ.diff.assert_not_called()
    differ.diff_strings.assert_not_called()


@pytest.mark.parametrize("field", ["content", "plugin_id"])
def test_detect_rejects_escaped_lone_surrogates(field) -> None:
    client, differ = _client()
    response = client.post(
        "/detect", content=_raw({"content": "x"}, field, "\\ud800"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    differ.detect_all.assert_not_called()


def test_paired_surrogates_and_non_bmp_text_are_accepted() -> None:
    client, differ = _client()
    body = (
        b'{"old": "s = \\"\\ud83d\\ude00\\"", '
        b'"new": "s = \\"\xf0\x9f\x98\x81\\"", "filename": "f.py"}'
    )
    response = client.post("/diff", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 200
    source = differ.diff.call_args.args[0]
    assert source.get_content()[0] == 's = "\U0001F600"'


def test_an_escaped_backslash_before_u_is_not_a_surrogate() -> None:
    client, differ = _client()
    body = b'{"old": "\\\\ud800", "new": "x", "filename": "f.py"}'
    response = client.post("/diff", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 200
