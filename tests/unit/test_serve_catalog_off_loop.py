"""#320: cold language-catalog loading must not block unrelated requests on the event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")

from intentumdiff.serve import create_app  # noqa: E402

_SLOW_SECONDS = 1.5


def _slow_differ(release: threading.Event) -> MagicMock:
    differ = MagicMock()

    def slow(result):
        def call():
            release.wait(_SLOW_SECONDS)
            return result
        return call

    differ.supported_languages = slow(["python", "rust"])
    differ.language_info = slow([])
    return differ


@pytest.mark.parametrize("path", ["/languages", "/language-info"])
def test_slow_catalog_does_not_block_the_root_page(path) -> None:
    release = threading.Event()
    app = create_app(differ=_slow_differ(release))

    async def scenario() -> tuple[float, int, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = time.monotonic()
            catalog = asyncio.create_task(client.get(path))
            await asyncio.sleep(0.05)  # the catalog request is now inside its handler
            root = await client.get("/")
            elapsed = time.monotonic() - started
            release.set()
            return elapsed, root.status_code, (await catalog).status_code

    elapsed, root_status, catalog_status = asyncio.run(scenario())
    assert root_status == 200 and catalog_status == 200
    assert elapsed < _SLOW_SECONDS / 2, f"GET / waited {elapsed:.2f}s behind {path}"


def test_catalog_results_are_unchanged() -> None:
    from fastapi.testclient import TestClient

    release = threading.Event()
    release.set()
    client = TestClient(create_app(differ=_slow_differ(release)))
    assert client.get("/languages").json() == {"languages": ["python", "rust"]}
    assert client.get("/language-info").json() == {"languages": []}


def test_cold_real_runtime_catalog_is_complete() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app())  # a fresh SemanticDiffer: the cold path
    languages = client.get("/languages").json()["languages"]
    assert "python" in languages and "rust" in languages
    assert languages == sorted(set(languages))
    info = client.get("/language-info").json()["languages"]
    assert {group["language"] for group in info} >= {"python", "rust"}
