"""Тесты SearXNGClient — с моком httpx."""
import pytest

from deep_research.config import SearXNGConfig
from deep_research.tools import SearXNGClient


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.last_params = None

    async def get(self, path, params=None):
        self.last_params = params or {}
        return _FakeResp(self._payload)

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_search_parses_results():
    cfg = SearXNGConfig(url="http://example.test", language="en")
    client = SearXNGClient(cfg)
    fake = _FakeClient(
        {
            "results": [
                {
                    "title": "Hello",
                    "url": "https://example.com/hello",
                    "content": "Snippet",
                    "engine": "google",
                    "score": 1.2,
                },
                {
                    "title": "World",
                    "url": "https://example.com/world",
                    "content": "Snippet 2",
                    "engine": "bing",
                    "score": 0.9,
                },
            ]
        }
    )
    client._client = fake  # подменяем httpx-клиент

    results = await client.search("hello world", max_results=5)

    assert len(results) == 2
    assert results[0].title == "Hello"
    assert results[0].url == "https://example.com/hello"
    assert results[0].query == "hello world"
    assert fake.last_params["q"] == "hello world"
    assert fake.last_params["language"] == "en"


@pytest.mark.asyncio
async def test_search_respects_max_results():
    cfg = SearXNGConfig(url="http://example.test")
    client = SearXNGClient(cfg)
    client._client = _FakeClient(
        {
            "results": [
                {"title": str(i), "url": f"https://e.test/{i}", "content": ""}
                for i in range(20)
            ]
        }
    )
    results = await client.search("x", max_results=3)
    assert len(results) == 3
