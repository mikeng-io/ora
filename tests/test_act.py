"""Dry-run only. Build the request for a listed room and
assert its shape; refuse an unlisted one. No real send tonight — there is
no HTTP call anywhere in this module or its test."""

from __future__ import annotations

from loop.act import (
    build_signal_request,
    build_whatsapp_request,
    find_mentions,
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
        env=Env(),
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
