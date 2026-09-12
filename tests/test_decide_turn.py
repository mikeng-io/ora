"""The participation turn against a fake model client (the
`tests/test_journey.py` pattern) and a small recording fake store (the
`tests/test_vision.py` pattern). No network, no real Postgres.

The one test that must redden if the ungrounded-speak brake is removed:
`test_ungrounded_speak_is_hold_and_never_delivers`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from loop import decide_turn
from loop.config import Clocks, Config, Env, Room
from loop.people import PeopleDirectory, Person
from loop.render import TranscriptRow

NOW = datetime(2026, 9, 12, 20, 0, 0, tzinfo=UTC)

SIGNAL_ROOM = "listed-group-id"
WORKSPACE = "demo"

MIKE = Person(id="mike", name="Mike", signal="+85290000000")
PEOPLE = PeopleDirectory([MIKE])


def _config() -> Config:
    return Config(
        env=Env(),
        clocks=Clocks(),
        workspaces={},
        rooms={("signal", SIGNAL_ROOM): Room("signal", SIGNAL_ROOM, "Signal")},
    )


class _FakeDeliverConfig:
    """`loop/act.py::deliver` reads `config.room_for`, `config.workspace`,
    and the base urls through `config.env` — the real `Config` keeps them
    on its `Env`, not flat on itself. This double mirrors that shape
    exactly; a double that flattened them would keep passing while the
    real call raised AttributeError on the first live send."""

    def __init__(self, *, base_url: str = "http://fake-signal") -> None:
        self._rooms = {("signal", SIGNAL_ROOM): Room("signal", SIGNAL_ROOM, "Signal")}
        self.env = SimpleNamespace(signal_base_url=base_url, whatsapp_base_url=base_url)
        self.workspace = WORKSPACE

    def room_for(self, platform: str, conversation_id: str) -> Room | None:
        return self._rooms.get((platform, conversation_id))


@dataclass
class _FakeResponse:
    status: int = 200

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakeSession:
    """Stands in for `aiohttp.ClientSession` so `deliver()` never touches
    the network. `posts` records every call for assertions."""

    status: int = 200
    posts: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse(status=self.status)


class _FakeModel:
    """Keyed by stage, like `tests/test_journey.py::FakeModelClient` —
    but this one can also raise, to script the "transport error out of
    complete_json" failure mode."""

    def __init__(self, completion: Any = None, *, raises: Exception | None = None) -> None:
        self._completion = completion
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        assert self._completion is not None
        return self._completion


@dataclass
class _Completion:
    content: str
    finish_reason: str = "stop"
    ok: bool = True
    call_id: int | None = 7
    error_kind: str | None = None


class _FakeStore:
    """Records every `decisions` and `messages` insert; holds reminder
    rows in memory so a cancel's `UPDATE ... RETURNING id` can be answered
    without a real Postgres."""

    def __init__(self, reminders: dict[str, dict[str, Any]] | None = None) -> None:
        self.decisions: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.reminders = reminders or {}

    async def fetchval(self, query: str, *args: Any) -> int:
        q = query.strip()
        if q.startswith("INSERT INTO decisions"):
            row = {
                "workspace": args[0], "writer": args[1], "verdict": args[2],
                "platform": args[3], "conversation_id": args[4], "note_id": args[5],
                "reminder_id": args[6], "grounded_on": args[7], "reason": args[8],
                "call_id": args[9], "ts": args[10],
            }
            self.decisions.append(row)
            return len(self.decisions)
        if q.startswith("INSERT INTO messages"):
            row = {
                "platform": args[0], "conversation_id": args[1], "workspace": args[2],
                "delivery_status": args[3], "ts": args[4], "body": args[5], "body_len": args[6],
            }
            self.messages.append(row)
            return len(self.messages)
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = query.strip()
        if q.startswith("UPDATE reminders"):
            resolved_at, reminder_id, workspace = args
            reminder = self.reminders.get(reminder_id)
            if reminder and reminder["workspace"] == workspace and reminder["state"] == "pending":
                reminder["state"] = "cancelled"
                reminder["resolved_at"] = resolved_at
                reminder["resolved_by"] = "participation:cancel"
                return {"id": reminder_id}
            return None
        raise AssertionError(f"unexpected fetchrow: {query}")


def _decide_kwargs(**overrides: Any) -> dict[str, Any]:
    base = dict(
        config=_FakeDeliverConfig(),
        workspace=WORKSPACE,
        platform="signal",
        conversation_id=SIGNAL_ROOM,
        now=NOW,
        transcript=[TranscriptRow(sender_label="Mike", body="hey", ts=NOW)],
        people=PEOPLE,
        session=_FakeSession(),
    )
    base.update(overrides)
    return base


async def test_speak_with_valid_grounded_on_calls_deliver_and_records_spoke() -> None:
    body = '{"verdict": "speak", "text": "6pm works", "grounded_on": "note:N1"}'
    model = _FakeModel(_Completion(content=body))
    store = _FakeStore()
    session = _FakeSession()

    result = await decide_turn.decide(
        model=model, store=store, **_decide_kwargs(session=session)
    )

    assert result.verdict == "spoke"
    assert result.grounded_on == "note:N1"
    assert result.note_id == "N1"
    assert result.call_id == 7
    assert len(session.posts) == 1  # deliver actually sent it
    assert len(store.messages) == 1
    assert store.decisions[0]["verdict"] == "spoke"
    assert store.decisions[0]["writer"] == "participation"
    assert store.decisions[0]["call_id"] == 7


async def test_ungrounded_speak_is_hold_and_never_delivers() -> None:
    """THE BRAKE (ORA-10): a speak with no grounded_on must never reach
    `deliver` and must be recorded as 'silent', not 'spoke'. Revert the
    `_grounded_on_is_valid` check in `loop/decide_turn.py` and this test
    reddens."""
    body = '{"verdict": "speak", "text": "6pm works"}'
    model = _FakeModel(_Completion(content=body))
    store = _FakeStore()
    session = _FakeSession()

    result = await decide_turn.decide(
        model=model, store=store, **_decide_kwargs(session=session)
    )

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_UNGROUNDED
    assert session.posts == []  # never delivered
    assert store.messages == []
    assert store.decisions[0]["verdict"] == "silent"


async def test_speak_with_empty_grounded_on_string_is_also_a_hold() -> None:
    body = '{"verdict": "speak", "text": "6pm works", "grounded_on": ""}'
    model = _FakeModel(_Completion(content=body))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_UNGROUNDED


async def test_hold_records_silent_and_never_calls_deliver() -> None:
    model = _FakeModel(_Completion(content='{"verdict": "hold"}'))
    store = _FakeStore()
    session = _FakeSession()

    result = await decide_turn.decide(
        model=model, store=store, **_decide_kwargs(session=session)
    )

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_HOLD
    assert session.posts == []
    assert store.decisions[0]["verdict"] == "silent"


async def test_cancel_flips_the_reminder_and_records_cancelled() -> None:
    reminders = {
        "R2": {"workspace": WORKSPACE, "state": "pending"},
    }
    model = _FakeModel(_Completion(content='{"verdict": "cancel", "reminder_id": "R2"}'))
    store = _FakeStore(reminders=reminders)

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "cancelled"
    assert result.reminder_id == "R2"
    assert reminders["R2"]["state"] == "cancelled"
    assert reminders["R2"]["resolved_by"] == "participation:cancel"
    assert reminders["R2"]["resolved_at"] == NOW
    assert store.decisions[0]["verdict"] == "cancelled"
    assert store.decisions[0]["reminder_id"] == "R2"


async def test_cancel_of_an_unknown_reminder_is_not_honoured() -> None:
    model = _FakeModel(_Completion(content='{"verdict": "cancel", "reminder_id": "R9"}'))
    store = _FakeStore(reminders={})

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_CANCEL_NOT_FOUND
    assert store.decisions[0]["verdict"] == "silent"


async def test_cancel_of_an_already_resolved_reminder_is_not_honoured() -> None:
    reminders = {"R2": {"workspace": WORKSPACE, "state": "done"}}
    model = _FakeModel(_Completion(content='{"verdict": "cancel", "reminder_id": "R2"}'))
    store = _FakeStore(reminders=reminders)

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert reminders["R2"]["state"] == "done"  # untouched


# --- failure modes: every one of these must fail closed to 'silent' -----


async def test_timeout_degrades_to_hold() -> None:
    completion = _Completion(content="", finish_reason="timeout", ok=False, error_kind="timeout")
    model = _FakeModel(completion)
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == "held_timeout"
    assert result.call_id == 7


async def test_an_exception_out_of_complete_json_degrades_to_hold() -> None:
    model = _FakeModel(raises=ConnectionError("connection reset"))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert "ConnectionError" in result.reason
    assert store.decisions[0]["verdict"] == "silent"


async def test_bad_json_degrades_to_hold() -> None:
    model = _FakeModel(_Completion(content="not json at all"))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_BAD_JSON


async def test_a_non_stop_finish_degrades_to_hold() -> None:
    completion = _Completion(
        content='{"verdict": "speak"', finish_reason="length", ok=False, error_kind="length"
    )
    model = _FakeModel(completion)
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == "held_length"


async def test_an_unknown_verdict_degrades_to_hold() -> None:
    model = _FakeModel(_Completion(content='{"verdict": "shout"}'))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_UNKNOWN_VERDICT


async def test_call_id_reaches_the_decisions_row_on_every_path() -> None:
    body = '{"verdict": "speak", "text": "ok", "grounded_on": "standing"}'
    model = _FakeModel(_Completion(content=body, call_id=42))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.call_id == 42
    assert store.decisions[0]["call_id"] == 42


async def test_speak_grounded_on_standing_carries_no_note_id() -> None:
    body = '{"verdict": "speak", "text": "ok", "grounded_on": "standing"}'
    model = _FakeModel(_Completion(content=body))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "spoke"
    assert result.note_id is None
    assert store.decisions[0]["note_id"] is None


async def test_speak_whose_delivery_fails_records_failed_not_spoke() -> None:
    body = '{"verdict": "speak", "text": "ok", "grounded_on": "note:N1"}'
    model = _FakeModel(_Completion(content=body))
    store = _FakeStore()
    session = _FakeSession(status=500)  # deliver() will see HTTP 500 -> not sent

    result = await decide_turn.decide(
        model=model, store=store, **_decide_kwargs(session=session)
    )

    assert result.verdict == "failed"
    assert result.delivery_ok is False
    assert store.decisions[0]["verdict"] == "failed"


async def test_speak_to_an_unlisted_room_never_reaches_the_wire() -> None:
    """deliver() itself re-checks the allowlist; a room decide_turn was
    somehow called for that isn't in rooms.toml must still not send."""
    body = '{"verdict": "speak", "text": "ok", "grounded_on": "note:N1"}'
    model = _FakeModel(_Completion(content=body))
    store = _FakeStore()
    session = _FakeSession()

    result = await decide_turn.decide(
        model=model, store=store,
        **_decide_kwargs(conversation_id="not-listed", session=session),
    )

    assert result.verdict == "failed"
    assert session.posts == []


@pytest.mark.parametrize(
    "payload",
    ['{"verdict": "speak", "grounded_on": "note:N1"}', '{"verdict": "speak", "text": ""}'],
)
async def test_speak_missing_or_empty_text_degrades_to_hold(payload: str) -> None:
    model = _FakeModel(_Completion(content=payload))
    store = _FakeStore()

    result = await decide_turn.decide(model=model, store=store, **_decide_kwargs())

    assert result.verdict == "silent"
    assert result.reason == decide_turn.REASON_EMPTY_TEXT
