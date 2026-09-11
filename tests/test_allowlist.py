"""design/02 §3's test: a message from an unlisted room reaches neither
`messages` nor Honcho; a `speak()` to an unlisted room is refused without a
request; both counted. This half covers Observe (design/07 item 9) — the
`act.py` half lands with item 10.

Reverting the allowlist check (or the own-send/syncMessage drop) must turn
this red — that is what makes it a brake and not decoration.
"""

from __future__ import annotations

from rich.console import Console

from loop.config import Clocks, Config, Env, Room
from loop.logging import LogSink
from loop.observe_signal import SignalObserver
from loop.observe_signal import parse_event as parse_signal_event
from loop.observe_whatsapp import WhatsAppObserver
from loop.observe_whatsapp import parse_event as parse_whatsapp_event

OWN_ACCOUNT = "+85290000000"
LISTED_SIGNAL_GROUP = "listed-group-id"
LISTED_WHATSAPP_JID = "listed-jid@g.us"


class RecordingStore:
    def __init__(self) -> None:
        self.inserted: list[tuple] = []

    async def execute(self, _query: str, *args: object) -> str:
        self.inserted.append(args)
        return "INSERT 0 1"


def _config(rooms: dict[tuple[str, str], Room] | None = None) -> Config:
    return Config(
        env=Env(signal_account=OWN_ACCOUNT),
        clocks=Clocks(),
        workspaces={},
        rooms=rooms or {},
    )


def _sink(tmp_path) -> tuple[LogSink, list]:
    events: list = []
    sink = LogSink(json_path=tmp_path / "ora.log", console=Console(quiet=True))
    original = sink.event

    def spy(stage, room, what, **kwargs):
        result = original(stage, room, what, **kwargs)
        if result is not None:  # LogSink dedups DROP; only record what it actually emitted
            events.append((stage, room, what, kwargs))
        return result

    sink.event = spy  # type: ignore[method-assign]
    return sink, events


def _signal_envelope(group_id: str, source: str, text: str) -> dict:
    return {
        "envelope": {
            "source": source,
            "sourceName": "Someone",
            "timestamp": 1_757_600_000_000,
            "dataMessage": {
                "message": text,
                "timestamp": 1_757_600_000_000,
                "groupInfo": {"groupId": group_id},
            },
        }
    }


def _whatsapp_message(chat_jid: str, sender: str, text: str, *, from_me: bool = False) -> dict:
    return {
        "type": "message",
        "chat_jid": chat_jid,
        "sender_jid": sender,
        "push_name": "Someone",
        "text": text,
        "from_me": from_me,
        "timestamp_ms": 1_757_600_000_000,
    }


async def test_unlisted_signal_room_never_reaches_the_store(tmp_path) -> None:
    config = _config(rooms={})  # nothing listed
    store = RecordingStore()
    sink, events = _sink(tmp_path)
    observer = SignalObserver(config, store, sink)

    await observer.handle_event(_signal_envelope("unlisted-group", "+85211111111", "hello"))

    assert store.inserted == []
    assert any(e[0] == "DROP" for e in events)


async def test_unlisted_whatsapp_room_never_reaches_the_store(tmp_path) -> None:
    config = _config(rooms={})
    store = RecordingStore()
    sink, events = _sink(tmp_path)
    observer = WhatsAppObserver(config, store, sink)

    await observer.handle_event(_whatsapp_message("unlisted@g.us", "+85211111111", "hello"))

    assert store.inserted == []
    assert any(e[0] == "DROP" for e in events)


async def test_listed_signal_room_reaches_the_store(tmp_path) -> None:
    config = _config(
        rooms={("signal", LISTED_SIGNAL_GROUP): Room("signal", LISTED_SIGNAL_GROUP, "Signal")}
    )
    store = RecordingStore()
    sink, _ = _sink(tmp_path)
    observer = SignalObserver(config, store, sink)

    await observer.handle_event(
        _signal_envelope(LISTED_SIGNAL_GROUP, "+85211111111", "hello")
    )

    assert len(store.inserted) == 1


async def test_listed_whatsapp_room_reaches_the_store(tmp_path) -> None:
    config = _config(
        rooms={("whatsapp", LISTED_WHATSAPP_JID): Room("whatsapp", LISTED_WHATSAPP_JID, "WhatsApp")}
    )
    store = RecordingStore()
    sink, _ = _sink(tmp_path)
    observer = WhatsAppObserver(config, store, sink)

    await observer.handle_event(
        _whatsapp_message(LISTED_WHATSAPP_JID, "+85211111111", "hello")
    )

    assert len(store.inserted) == 1


async def test_drop_is_logged_once_per_room_never_again(tmp_path) -> None:
    config = _config(rooms={})
    store = RecordingStore()
    sink, events = _sink(tmp_path)
    observer = SignalObserver(config, store, sink)

    await observer.handle_event(_signal_envelope("unlisted-group", "+85211111111", "a"))
    await observer.handle_event(_signal_envelope("unlisted-group", "+85211111111", "b"))

    drop_events = [e for e in events if e[0] == "DROP"]
    assert len(drop_events) == 1


# --- own-send / syncMessage drop (Opus audit finding 12) --------------------


def test_signal_sync_message_is_never_ingested() -> None:
    """A syncMessage carries no `dataMessage` — `_data_message` returns
    None and the row is dropped BEFORE the allowlist ever runs."""
    sync_event = {
        "envelope": {
            "source": OWN_ACCOUNT,
            "syncMessage": {
                "sentMessage": {
                    "message": "her own send, echoed back",
                    "groupInfo": {"groupId": LISTED_SIGNAL_GROUP},
                }
            },
        }
    }
    assert parse_signal_event(sync_event, own_account=OWN_ACCOUNT) is None


def test_signal_own_account_data_message_is_dropped_even_in_a_listed_room() -> None:
    """Defense in depth: even a `dataMessage` whose `source` IS the own
    account is dropped — the allowlist alone cannot catch this, because the
    conversation_id is a LISTED room."""
    own_send = _signal_envelope(LISTED_SIGNAL_GROUP, OWN_ACCOUNT, "my own message")
    assert parse_signal_event(own_send, own_account=OWN_ACCOUNT) is None


def test_whatsapp_from_me_is_never_ingested_even_in_a_listed_room() -> None:
    own_send = _whatsapp_message(
        LISTED_WHATSAPP_JID, OWN_ACCOUNT, "my own message", from_me=True
    )
    assert parse_whatsapp_event(own_send) is None


async def test_own_sync_row_is_not_ingested_end_to_end(tmp_path) -> None:
    """The allowlist-drop test alone cannot prove this: a sync row's
    conversation_id IS the listed group, so only the parser's own-send rule
    catches it. Reverting `_data_message`'s syncMessage check (or the
    `own_account` check) must turn this red."""
    config = _config(
        rooms={("signal", LISTED_SIGNAL_GROUP): Room("signal", LISTED_SIGNAL_GROUP, "Signal")}
    )
    store = RecordingStore()
    sink, events = _sink(tmp_path)
    observer = SignalObserver(config, store, sink)

    sync_event = {
        "envelope": {
            "source": OWN_ACCOUNT,
            "syncMessage": {
                "sentMessage": {
                    "message": "her own send",
                    "groupInfo": {"groupId": LISTED_SIGNAL_GROUP},
                }
            },
        }
    }
    await observer.handle_event(sync_event)

    assert store.inserted == []
    assert not any(e[0] == "DROP" for e in events)  # never reached the allowlist at all


def test_dm_without_group_info_is_dropped() -> None:
    dm_event = {
        "envelope": {
            "source": "+85211111111",
            "dataMessage": {"message": "a dm", "timestamp": 1_757_600_000_000},
        }
    }
    assert parse_signal_event(dm_event, own_account=OWN_ACCOUNT) is None


def test_whatsapp_broadcast_is_dropped() -> None:
    broadcast = _whatsapp_message("status@broadcast", "+85211111111", "status update")
    assert parse_whatsapp_event(broadcast) is None
