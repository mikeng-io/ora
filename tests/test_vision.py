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
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
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
