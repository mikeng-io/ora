import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from loop.model import ModelClient


@dataclass
class _Usage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


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
    usage: _Usage | None = None


class _FakeCompletions:
    def __init__(self, response: _Response | None = None, *, hang: bool = False) -> None:
        self._response = response
        self._hang = hang
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if self._hang:
            await asyncio.sleep(10)
        assert self._response is not None
        return self._response


@dataclass
class _FakeChat:
    completions: _FakeCompletions


@dataclass
class _FakeClient:
    chat: _FakeChat = field(init=False)
    completions: _FakeCompletions

    def __post_init__(self) -> None:
        self.chat = _FakeChat(completions=self.completions)


async def test_json_object_call_shape_and_reasoning_effort_pinned() -> None:
    fake = _FakeCompletions(
        _Response(choices=[_Choice(_Message('{"go": true}'), "stop")], usage=_Usage())
    )
    client = ModelClient(client=_FakeClient(completions=fake), model="deepseek-v4-flash")

    result = await client.complete_json(
        stage="gate", system="you are the gate", prompt="the room",
        reasoning_effort="none",
    )

    assert result.ok
    assert result.content == '{"go": true}'
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.call_id is None  # no store wired

    call = fake.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["reasoning_effort"] == "none"
    assert call["messages"][0] == {"role": "system", "content": "you are the gate"}
    assert call["messages"][1] == {"role": "user", "content": "the room"}


async def test_length_finish_reason_is_not_ok() -> None:
    fake = _FakeCompletions(
        _Response(choices=[_Choice(_Message('{"trunc'), "length")], usage=_Usage())
    )
    client = ModelClient(client=_FakeClient(completions=fake), model="deepseek-v4-flash")

    result = await client.complete_json(
        stage="turn", system="s", prompt="p", reasoning_effort="low"
    )

    assert not result.ok
    assert result.finish_reason == "length"
    assert result.error_kind == "length"


async def test_timeout_fails_closed() -> None:
    fake = _FakeCompletions(hang=True)
    client = ModelClient(
        client=_FakeClient(completions=fake), model="deepseek-v4-flash", timeout_seconds=0.05
    )

    result = await client.complete_json(
        stage="gate", system="s", prompt="p", reasoning_effort="none"
    )

    assert not result.ok
    assert result.finish_reason == "timeout"
    assert result.error_kind == "timeout"
    assert result.content == ""


async def test_missing_finish_reason_is_not_assumed_stop() -> None:
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message("x"), None)]))
    client = ModelClient(client=_FakeClient(completions=fake), model="deepseek-v4-flash")

    result = await client.complete_json(
        stage="gate", system="s", prompt="p", reasoning_effort="none"
    )

    assert result.finish_reason == ""
    assert not result.ok


@pytest.mark.parametrize("reasoning", ["none", "low"])
async def test_reasoning_effort_is_passed_through_unmodified(reasoning: str) -> None:
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message("{}"), "stop")]))
    client = ModelClient(client=_FakeClient(completions=fake), model="deepseek-v4-flash")

    await client.complete_json(stage="turn", system="s", prompt="p", reasoning_effort=reasoning)

    assert fake.calls[0]["reasoning_effort"] == reasoning
