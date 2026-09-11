from dataclasses import dataclass
from typing import Any

from loop.vision import comprehend


@dataclass
class _Message:
    content: str | None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str | None


@dataclass
class _Response:
    choices: list[_Choice]


class _FakeCompletions:
    def __init__(
        self, response: _Response | None = None, *, raises: Exception | None = None
    ) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        assert self._response is not None
        return self._response


@dataclass
class _FakeChat:
    completions: _FakeCompletions


@dataclass
class _FakeClient:
    completions: _FakeCompletions

    def __post_init__(self) -> None:
        self.chat = _FakeChat(completions=self.completions)


async def test_comprehend_parses_all_three_fields() -> None:
    body = '{"title": "a cat", "description": "an orange cat on a sofa", "content": ""}'
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message(body), "stop")]))
    client = _FakeClient(completions=fake)

    result = await comprehend(client, data=b"fake bytes", media_type="image/png")

    assert result.ok
    assert result.title == "a cat"
    assert result.description == "an orange cat on a sofa"
    assert result.content == ""


async def test_comprehend_sends_an_image_url_content_block() -> None:
    body = '{"title":"x","description":"y","content":""}'
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message(body), "stop")]))
    client = _FakeClient(completions=fake)

    await comprehend(client, data=b"\x89PNG", media_type="image/png")

    call = fake.calls[0]
    assert call["model"] == "glm-5.3-flash"
    assert call["response_format"] == {"type": "json_object"}
    user_content = call["messages"][1]["content"]
    image_block = next(b for b in user_content if b["type"] == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")


async def test_comprehend_is_not_ok_on_a_non_stop_finish() -> None:
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message(""), "length")]))
    client = _FakeClient(completions=fake)

    result = await comprehend(client, data=b"x", media_type="image/jpeg")

    assert not result.ok
    assert result.finish_reason == "length"


async def test_comprehend_is_not_ok_on_unparseable_json() -> None:
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message("not json"), "stop")]))
    client = _FakeClient(completions=fake)

    result = await comprehend(client, data=b"x", media_type="image/jpeg")

    assert not result.ok


class _RecordingStore:
    def __init__(self) -> None:
        self.inserted: list[tuple] = []

    async def fetchval(self, _query: str, *args: object) -> int:
        self.inserted.append(args)
        return len(self.inserted)


async def test_comprehend_writes_a_model_calls_row_when_a_store_is_given() -> None:
    body = '{"title":"x","description":"y","content":""}'
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message(body), "stop")]))
    client = _FakeClient(completions=fake)
    store = _RecordingStore()

    result = await comprehend(client, data=b"x", media_type="image/png", store=store)

    assert result.call_id == 1
    assert len(store.inserted) == 1


async def test_comprehend_writes_a_model_calls_row_even_when_not_ok() -> None:
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message(""), "length")]))
    client = _FakeClient(completions=fake)
    store = _RecordingStore()

    result = await comprehend(client, data=b"x", media_type="image/png", store=store)

    assert not result.ok
    assert result.call_id == 1
    assert len(store.inserted) == 1


# --- never raises (Opus review: only TimeoutError was caught in the first cut) --


async def test_comprehend_never_raises_on_a_connection_error() -> None:
    """A real AsyncOpenAI raises APIConnectionError/RateLimitError/
    APIStatusError, none of which is a TimeoutError. comprehend() must
    degrade to ok=False, not propagate — R-5's discipline, at the source
    rather than relying on every caller's own try/except."""
    fake = _FakeCompletions(raises=ConnectionError("connection reset"))
    client = _FakeClient(completions=fake)

    result = await comprehend(client, data=b"x", media_type="image/png")

    assert not result.ok
    assert result.finish_reason == "ConnectionError"


async def test_comprehend_never_raises_on_empty_choices() -> None:
    """An empty `choices` list raises IndexError on `response.choices[0]`
    in the naive form — must degrade, not propagate."""
    fake = _FakeCompletions(_Response(choices=[]))
    client = _FakeClient(completions=fake)

    result = await comprehend(client, data=b"x", media_type="image/png")

    assert not result.ok
    assert result.finish_reason == "empty_choices"


async def test_prompt_sha_differs_between_two_different_images() -> None:
    """Review finding (should-fix): prompt_sha used to hash only the fixed
    system prompt + media_type, so every image in a run shared one digest
    — a wrong description was not "one join away from its prompt digest",
    it was indistinguishable from every other image's. Now it must fold
    in the actual bytes."""
    body = '{"title":"x","description":"y","content":""}'
    store = _RecordingStore()
    response = _Response(choices=[_Choice(_Message(body), "stop")])
    client_a = _FakeClient(completions=_FakeCompletions(response))
    client_b = _FakeClient(completions=_FakeCompletions(response))

    await comprehend(client_a, data=b"image one bytes", media_type="image/png", store=store)
    await comprehend(
        client_b, data=b"image two, totally different", media_type="image/png", store=store
    )

    prompt_shas = [args[3] for args in store.inserted]  # 4th bound param is prompt_sha
    assert prompt_shas[0] != prompt_shas[1]
