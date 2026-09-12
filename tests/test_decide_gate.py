"""The Relevance Gate: fail-closed on every error path, `go`/`no_go` on a
clean one, one `gate_log` row written every time.

Runs against the REAL `ModelClient` (grounded on the actual contract, not a
proxy of it — CMS's "a grep standing in for the feature works is not a
measurement") wired to a fake OpenAI-shaped transport, the same pattern
`tests/test_model.py` uses. No network, no real Postgres: `_RecordingStore`
fakes both `model_calls` (written by `ModelClient` itself) and `gate_log`
(written by `decide_gate`), split by table name so a test can tell which
row is which and that `call_id` really does join the two.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loop.decide_gate import GateResult, judge, load_system_prompt
from loop.model import ModelClient
from loop.render import StandingEntry, TranscriptRow

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


def _rows() -> list[TranscriptRow]:
    return [
        TranscriptRow(sender_label="Mike", body="我哋上次去咗邊度食？", ts=NOW),
        TranscriptRow(sender_label="Alan", body="唔記得喎", ts=NOW),
    ]


# --- fake OpenAI-shaped transport (test_model.py's pattern) -----------------


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
    usage: None = None


class _FakeCompletions:
    def __init__(
        self,
        response: _Response | None = None,
        *,
        raises: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self._response = response
        self._raises = raises
        self._hang = hang
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if self._hang:
            await asyncio.sleep(10)
        if self._raises is not None:
            raise self._raises
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


def _stop(body: str) -> _Response:
    return _Response(choices=[_Choice(_Message(body), "stop")])


# --- fake store, split by table so call_id joins are checkable -------------


class _RecordingStore:
    def __init__(self) -> None:
        self.model_calls: list[tuple[Any, ...]] = []
        self.gate_log: list[tuple[Any, ...]] = []

    async def fetchval(self, query: str, *args: Any) -> int:
        if "model_calls" in query:
            self.model_calls.append(args)
            return len(self.model_calls)
        if "gate_log" in query:
            self.gate_log.append(args)
            return len(self.gate_log)
        raise AssertionError(f"unexpected query: {query}")


def _client(fake: _FakeCompletions, store: _RecordingStore, **kwargs: Any) -> ModelClient:
    return ModelClient(
        client=_FakeClient(completions=fake), model="deepseek-v4-flash", store=store, **kwargs
    )


async def _judge(model: ModelClient, store: _RecordingStore, **overrides: Any) -> GateResult:
    kwargs: dict[str, Any] = {
        "platform": "signal",
        "conversation_id": "room-1",
        "rows": _rows(),
        "standing": StandingEntry(body="looking for last meal", updated_at=NOW),
        "window_rows": 2,
        "window_end_row_id": 42,
        "now": NOW,
    }
    kwargs.update(overrides)
    return await judge(model, store, **kwargs)


# --- load_system_prompt: strips the YAML frontmatter ------------------------


def test_load_system_prompt_strips_frontmatter() -> None:
    system = load_system_prompt()
    assert "---" not in system.splitlines()[0]
    assert "name: participation" not in system
    assert "You are a relevance gate" in system


# --- the two clean paths -----------------------------------------------------


async def test_go_verdict_parses() -> None:
    fake = _FakeCompletions(_stop('{"verdict": "go", "relevance_score": 1.0}'))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert result.verdict == "go"
    assert result.score == 1.0
    assert result.error_kind is None
    assert result.call_id == 1


async def test_no_go_verdict_parses() -> None:
    fake = _FakeCompletions(_stop('{"verdict": "no_go", "relevance_score": 0.0}'))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert result.verdict == "no_go"
    assert result.score == 0.0
    assert result.error_kind is None  # a genuine no_go is not a failure


# --- every failure mode degrades to no_go, independently --------------------


async def test_timeout_fails_closed() -> None:
    fake = _FakeCompletions(hang=True)
    store = _RecordingStore()
    model = _client(fake, store, timeout_seconds=0.05)

    result = await _judge(model, store, timeout_seconds=0.05)

    assert result.verdict == "no_go"
    assert result.error_kind == "timeout"


async def test_transport_exception_fails_closed() -> None:
    """`ModelClient` only catches `TimeoutError` around its own call — a
    real `AsyncOpenAI` failure mode (connection reset, rate limit) would
    otherwise propagate. decide_gate must catch it instead of crashing the
    loop (rule 2)."""
    fake = _FakeCompletions(raises=ConnectionError("connection reset"))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert result.verdict == "no_go"
    assert result.error_kind == "ConnectionError"
    assert result.call_id is None  # the exception pre-empted ModelClient's own record


async def test_non_stop_finish_reason_fails_closed() -> None:
    fake = _FakeCompletions(_Response(choices=[_Choice(_Message(""), "length")]))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert result.verdict == "no_go"
    assert result.error_kind == "length"
    assert result.call_id == 1  # ModelClient still recorded this one


async def test_unparseable_json_fails_closed() -> None:
    fake = _FakeCompletions(_stop("not json"))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert result.verdict == "no_go"
    assert result.error_kind == "unparseable_json"
    assert result.call_id == 1


async def test_unknown_verdict_fails_closed() -> None:
    fake = _FakeCompletions(_stop('{"verdict": "maybe", "relevance_score": 0.5}'))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert result.verdict == "no_go"
    assert result.error_kind == "unknown_verdict"
    assert result.call_id == 1


# --- the gate_log row: written every time, carries call_id ------------------


async def test_gate_log_row_written_on_a_go_verdict() -> None:
    fake = _FakeCompletions(_stop('{"verdict": "go", "relevance_score": 1.0}'))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert len(store.gate_log) == 1
    row = store.gate_log[0]
    # (platform, conversation_id, verdict, score, error_kind, window_rows,
    #  window_end_row_id, call_id, ts) — loop/gate_log.py's own bind order
    assert row == ("signal", "room-1", "go", 1.0, None, 2, 42, 1, NOW)
    assert result.gate_log_id == 1


async def test_gate_log_row_written_on_a_failure() -> None:
    fake = _FakeCompletions(_stop("not json"))
    store = _RecordingStore()
    model = _client(fake, store)

    await _judge(model, store)

    assert len(store.gate_log) == 1
    row = store.gate_log[0]
    assert row[2] == "no_go"
    assert row[4] == "unparseable_json"
    assert row[7] == 1  # call_id still carried through on a parse failure


async def test_gate_log_call_id_joins_the_model_calls_row() -> None:
    fake = _FakeCompletions(_stop('{"verdict": "go", "relevance_score": 1.0}'))
    store = _RecordingStore()
    model = _client(fake, store)

    result = await _judge(model, store)

    assert len(store.model_calls) == 1
    assert result.call_id == 1
    # model_calls' own first bound column is `stage`, not an id — the join
    # is on the ROW id ModelClient returned (`RETURNING id`), which is what
    # fetchval's fake here counts: exactly one model_calls row exists, and
    # gate_log's call_id equals its 1-based position.
    assert store.gate_log[0][7] == 1


async def test_gate_never_writes_a_decisions_row() -> None:
    """DEC-179: the gate is not one of the four `decisions` writers. A
    store that only knows `model_calls` and `gate_log` queries is exactly
    the fixture that would raise AssertionError if decide_gate ever tried
    to insert into `decisions` — this test's fixture IS the assertion."""
    fake = _FakeCompletions(_stop('{"verdict": "go", "relevance_score": 1.0}'))
    store = _RecordingStore()
    model = _client(fake, store)

    await _judge(model, store)  # would raise inside _RecordingStore.fetchval otherwise

    assert len(store.gate_log) == 1
