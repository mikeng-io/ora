"""Fake-transport coverage for loop/search.py. The live
search + fetch are verified separately against api.exa.ai — see the item's
commit."""

from loop.search import ExaSearchProvider, clamp_chars, wrap


def test_clamp_chars_untouched_below_limit() -> None:
    text, truncated = clamp_chars("hello", 100)
    assert text == "hello"
    assert not truncated


def test_clamp_chars_cuts_above_limit() -> None:
    text, truncated = clamp_chars("hello world", 5)
    assert text == "hello"
    assert truncated


def test_wrap_escapes_untrusted_content() -> None:
    wrapped = wrap("http://x", "<script>alert(1)</script>")
    assert "<script>" not in wrapped
    assert "&lt;script&gt;" in wrapped
    assert 'source="http://x"' in wrapped


class _FakeResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        import json

        return json.dumps(self._body)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        return self._response

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_search_empty_results_is_nothing_found(monkeypatch) -> None:
    provider = ExaSearchProvider(api_key="fake")
    fake_session = _FakeSession(_FakeResponse(200, {"results": []}))
    monkeypatch.setattr("loop.search.aiohttp.ClientSession", lambda **kw: fake_session)

    result = await provider.search(query="anything")
    assert result["status"] == "nothing_found"


async def test_search_non_200_is_unavailable(monkeypatch) -> None:
    provider = ExaSearchProvider(api_key="fake")
    fake_session = _FakeSession(_FakeResponse(401, {}))
    monkeypatch.setattr("loop.search.aiohttp.ClientSession", lambda **kw: fake_session)

    result = await provider.search(query="anything")
    assert result["status"] == "unavailable"


async def test_search_ok_wraps_results_untrusted(monkeypatch) -> None:
    provider = ExaSearchProvider(api_key="fake")
    body = {"results": [{"url": "http://x", "title": "T", "text": "body text"}]}
    fake_session = _FakeSession(_FakeResponse(200, body))
    monkeypatch.setattr("loop.search.aiohttp.ClientSession", lambda **kw: fake_session)

    result = await provider.search(query="anything")
    assert result["status"] == "ok"
    assert "untrusted" in result["results"][0]["content"]


async def test_fetch_missing_results_key_is_unavailable(monkeypatch) -> None:
    provider = ExaSearchProvider(api_key="fake")
    fake_session = _FakeSession(_FakeResponse(200, {}))
    monkeypatch.setattr("loop.search.aiohttp.ClientSession", lambda **kw: fake_session)

    result = await provider.fetch(url="http://x")
    assert result["status"] == "unavailable"
