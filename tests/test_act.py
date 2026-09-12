"""`speak()`/`build_*_request` are dry-run only: build the request for a
listed room and assert its shape; refuse an unlisted one — no HTTP call.

`react()` is exercised with a fake `aiohttp.ClientSession` (same pattern as
`tests/test_decide_turn.py::_FakeSession`) so its HTTP path is covered
without a real network."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loop.act import (
    ReactionResult,
    build_signal_reaction_request,
    build_signal_request,
    build_whatsapp_reaction_request,
    build_whatsapp_request,
    find_mentions,
    react,
    speak,
)
from loop.config import Clocks, Config, Env, Room
from loop.people import PeopleDirectory, Person

MIKE = Person(id="mike", name="Mike", signal="+85290000000", whatsapp="85290000000@s.whatsapp.net")
PEOPLE = PeopleDirectory([MIKE])

LISTED_SIGNAL = "listed-group-id"
LISTED_WHATSAPP = "listed-jid@g.us"


def _config() -> Config:
    return Config(
        env=Env(
            signal_base_url="http://fake-signal",
            whatsapp_base_url="http://fake-whatsapp",
        ),
        clocks=Clocks(),
        workspaces={},
        rooms={
            ("signal", LISTED_SIGNAL): Room("signal", LISTED_SIGNAL, "Signal"),
            ("whatsapp", LISTED_WHATSAPP): Room("whatsapp", LISTED_WHATSAPP, "WhatsApp"),
        },
    )


def test_signal_request_shape_for_a_listed_room() -> None:
    request = build_signal_request(LISTED_SIGNAL, "hello there", PEOPLE)
    assert request.platform == "signal"
    assert request.method == "rpc"
    assert request.url_path == "/api/v1/rpc"
    assert request.payload["method"] == "send"
    assert request.payload["params"]["groupId"] == LISTED_SIGNAL
    assert request.payload["params"]["message"] == "hello there"
    assert "mention" not in request.payload["params"]  # absent, never empty


def test_signal_request_carries_a_resolved_mention() -> None:
    request = build_signal_request(LISTED_SIGNAL, "hey @Mike are you there", PEOPLE)
    mentions = request.payload["params"]["mention"]
    assert len(mentions) == 1
    start, length, recipient = mentions[0].split(":")
    assert recipient == MIKE.signal
    # "@Mike" starts at index 4, is 5 UTF-16 code units long (all ASCII)
    assert int(start) == 4
    assert int(length) == 5


def test_signal_unresolvable_name_goes_out_as_plain_text() -> None:
    request = build_signal_request(LISTED_SIGNAL, "hey @Nobody are you there", PEOPLE)
    assert "mention" not in request.payload["params"]
    assert request.payload["params"]["message"] == "hey @Nobody are you there"


def test_whatsapp_request_shape_for_a_listed_room() -> None:
    request = build_whatsapp_request(LISTED_WHATSAPP, "hello there", PEOPLE)
    assert request.platform == "whatsapp"
    assert request.method == "post"
    assert request.url_path == "/send"
    assert request.payload["jid"] == LISTED_WHATSAPP
    assert request.payload["text"] == "hello there"
    assert "mentions" not in request.payload


def test_whatsapp_request_carries_a_resolved_mention() -> None:
    request = build_whatsapp_request(LISTED_WHATSAPP, "hey @Mike are you there", PEOPLE)
    assert request.payload["mentions"] == [MIKE.whatsapp]
    assert "@85290000000" in request.payload["text"]
    assert "@Mike" not in request.payload["text"]


def test_speak_refuses_an_unlisted_room_without_building_a_request() -> None:
    config = _config()
    result = speak(config, "signal", "not-listed-at-all", "hello", PEOPLE)
    assert not result.ok
    assert result.request is None
    assert "rooms.toml" in result.reason


def test_speak_refuses_an_unlisted_whatsapp_room() -> None:
    config = _config()
    result = speak(config, "whatsapp", "not-listed@g.us", "hello", PEOPLE)
    assert not result.ok
    assert result.request is None


def test_speak_builds_a_request_for_a_listed_room() -> None:
    config = _config()
    result = speak(config, "signal", LISTED_SIGNAL, "hello", PEOPLE)
    assert result.ok
    assert result.request is not None
    assert result.request.payload["params"]["groupId"] == LISTED_SIGNAL


def test_mention_boundary_rejects_a_longer_word() -> None:
    """`@Mike` inside `@Mikeson` must not match — the ASCII word-boundary
    rule (the reference project `outbound._is_word`)."""
    assert find_mentions("hi @Mikeson", PEOPLE) == []


def test_longest_match_wins() -> None:
    people = PeopleDirectory(
        [
            Person(id="a", name="Mike", signal="+1"),
            Person(id="b", name="Mikey", signal="+2"),
        ]
    )
    found = find_mentions("hey @Mikey", people)
    assert len(found) == 1
    assert found[0][2] == "Mikey"


# --- react() -----------------------------------------------------------


def test_whatsapp_reaction_request_shape() -> None:
    request = build_whatsapp_reaction_request(LISTED_WHATSAPP, "3A6F8E77ECAA933F28C7", "\U0001f440")
    assert request.platform == "whatsapp"
    assert request.method == "post"
    assert request.url_path == "/react"
    assert request.payload == {
        "jid": LISTED_WHATSAPP,
        "message_id": "3A6F8E77ECAA933F28C7",
        "emoji": "\U0001f440",
    }


def test_signal_reaction_request_shape() -> None:
    request = build_signal_reaction_request(LISTED_SIGNAL, "+85211111111", 1_757_600_000_000, "✅")
    assert request.platform == "signal"
    assert request.method == "rpc"
    assert request.url_path == "/api/v1/rpc"
    assert request.payload["method"] == "sendReaction"
    assert request.payload["params"] == {
        "groupId": LISTED_SIGNAL,
        "emoji": "✅",
        "targetAuthor": "+85211111111",
        "targetTimestamp": 1_757_600_000_000,
    }


@dataclass
class _FakeReactResponse:
    status: int = 200

    async def __aenter__(self) -> _FakeReactResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakeReactSession:
    """Stands in for `aiohttp.ClientSession` so `react()` never touches the
    network — same pattern as `tests/test_decide_turn.py::_FakeSession`."""

    status: int = 200
    posts: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> _FakeReactResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return _FakeReactResponse(status=self.status)


class _RaisingSession:
    """A transport that blows up on `.post` — proves `react()` survives it."""

    def post(self, *args: object, **kwargs: object) -> Any:
        raise ConnectionError("simulated transport failure")


class _RecordingReactionStore:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def execute(self, _query: str, *args: object) -> str:
        self.rows.append(args)
        return "INSERT 0 1"


class _RaisingStore:
    async def execute(self, _query: str, *args: object) -> str:
        raise RuntimeError("simulated store outage")


async def test_react_whatsapp_builds_and_sends_the_right_request() -> None:
    config = _config()
    session = _FakeReactSession(status=200)

    result = await react(
        config, "whatsapp", LISTED_WHATSAPP, "\U0001f440",
        platform_message_id="3A6F8E77ECAA933F28C7", session=session,
    )

    assert result.ok
    assert session.posts == [
        {
            "url": "http://fake-whatsapp/react",
            "json": {
                "jid": LISTED_WHATSAPP,
                "message_id": "3A6F8E77ECAA933F28C7",
                "emoji": "\U0001f440",
            },
            "timeout": 15.0,
        }
    ]


async def test_react_signal_builds_and_sends_the_right_request() -> None:
    config = _config()
    session = _FakeReactSession(status=200)

    result = await react(
        config, "signal", LISTED_SIGNAL, "✅",
        platform_message_id="1757600000000", author="+85211111111", session=session,
    )

    assert result.ok
    assert len(session.posts) == 1
    payload = session.posts[0]["json"]
    assert payload["method"] == "sendReaction"
    assert payload["params"]["targetAuthor"] == "+85211111111"
    assert payload["params"]["targetTimestamp"] == 1_757_600_000_000
    assert payload["params"]["groupId"] == LISTED_SIGNAL


async def test_react_refuses_an_unlisted_room() -> None:
    config = _config()
    session = _FakeReactSession(status=200)

    result = await react(
        config, "whatsapp", "not-listed@g.us", "\U0001f440",
        platform_message_id="abc", session=session,
    )

    assert not result.ok
    assert "rooms.toml" in result.reason
    assert session.posts == []  # refused before it ever reached the wire


async def test_react_signal_without_author_is_refused_without_a_call() -> None:
    config = _config()
    session = _FakeReactSession(status=200)

    result = await react(
        config, "signal", LISTED_SIGNAL, "\U0001f440",
        platform_message_id="1757600000000", session=session,  # no author
    )

    assert not result.ok
    assert session.posts == []


async def test_react_non_200_is_reported_not_ok_and_does_not_raise() -> None:
    config = _config()
    session = _FakeReactSession(status=500)

    result = await react(
        config, "whatsapp", LISTED_WHATSAPP, "⚠️",
        platform_message_id="abc", session=session,
    )

    assert not result.ok
    assert "500" in result.reason


async def test_react_transport_error_never_raises() -> None:
    config = _config()

    result = await react(
        config, "whatsapp", LISTED_WHATSAPP, "\U0001f440",
        platform_message_id="abc", session=_RaisingSession(),
    )

    assert not result.ok
    assert result.reason  # some reason was captured, not swallowed silently


async def test_react_store_failure_never_raises_and_still_reports_the_send() -> None:
    """A reaction succeeding on the wire but failing to trace to `reactions`
    must still report the true wire result — the trace row is decoration on
    decoration, never load-bearing."""
    config = _config()
    session = _FakeReactSession(status=200)

    result = await react(
        config, "whatsapp", LISTED_WHATSAPP, "✅",
        platform_message_id="abc", session=session, store=_RaisingStore(),
    )

    assert result.ok  # the wire send still succeeded


async def test_react_writes_a_trace_row_when_a_store_is_given() -> None:
    config = _config()
    session = _FakeReactSession(status=200)
    store = _RecordingReactionStore()

    await react(
        config, "whatsapp", LISTED_WHATSAPP, "\U0001f440",
        platform_message_id="abc", session=session, store=store,
    )

    assert len(store.rows) == 1


def test_reaction_result_is_never_raised_as_an_exception() -> None:
    """Sanity: `ReactionResult` is a plain dataclass return value, not an
    exception type — `react()`'s contract is "return, never raise"."""
    assert not issubclass(ReactionResult, BaseException)
