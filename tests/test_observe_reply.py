"""Platform message id capture and quoted-reply detection, both
observers — what `loop/act.py::react()` needs to target a reaction, and
what a caller uses to decide whether a message is a reply to Ora.

Fakes only: a `RecordingStore` records the raw `INSERT INTO messages` args
tuple, no network, no Postgres — same discipline as
`tests/test_allowlist.py`.
"""

from __future__ import annotations

from rich.console import Console

from loop.config import Clocks, Config, Env, Room
from loop.logging import LogSink
from loop.observe_signal import SignalObserver
from loop.observe_signal import parse_event as parse_signal_event
from loop.observe_whatsapp import OWN_WHATSAPP_LID, OWN_WHATSAPP_PHONE, WhatsAppObserver
from loop.observe_whatsapp import parse_event as parse_whatsapp_event

OWN_SIGNAL_ACCOUNT = "+85290000000"
LISTED_SIGNAL_GROUP = "listed-group-id"
LISTED_WHATSAPP_JID = "listed-jid@g.us"

# messages columns, in the order both observers' INSERT lists them:
# platform, conversation_id, workspace, sender_id, person_id, ts, body,
# body_len, media_sha256, platform_message_id, is_reply_to_ora
_PLATFORM_MESSAGE_ID = 9
_IS_REPLY_TO_ORA = 10


class RecordingStore:
    def __init__(self) -> None:
        self.inserted: list[tuple] = []

    async def execute(self, _query: str, *args: object) -> str:
        self.inserted.append(args)
        return "INSERT 0 1"


def _sink(tmp_path) -> LogSink:
    return LogSink(json_path=tmp_path / "ora.log", console=Console(quiet=True))


def _signal_config() -> Config:
    return Config(
        env=Env(signal_account=OWN_SIGNAL_ACCOUNT),
        clocks=Clocks(),
        workspaces={},
        rooms={("signal", LISTED_SIGNAL_GROUP): Room("signal", LISTED_SIGNAL_GROUP, "Signal")},
    )


def _whatsapp_config() -> Config:
    return Config(
        env=Env(),
        clocks=Clocks(),
        workspaces={},
        rooms={
            ("whatsapp", LISTED_WHATSAPP_JID): Room("whatsapp", LISTED_WHATSAPP_JID, "WhatsApp"),
        },
    )


def _signal_envelope(
    text: str, *, source: str = "+85211111111", quote: dict | None = None
) -> dict:
    data: dict = {
        "message": text,
        "timestamp": 1_757_600_000_000,
        "groupInfo": {"groupId": LISTED_SIGNAL_GROUP},
    }
    if quote is not None:
        data["quote"] = quote
    return {
        "envelope": {
            "source": source,
            "sourceName": "Someone",
            "timestamp": 1_757_600_000_000,
            "dataMessage": data,
        }
    }


def _whatsapp_message(text: str, *, quoted: dict | None = None, event_id: str = "3A6F8E77") -> dict:
    return {
        "type": "message",
        "id": event_id,
        "chat_jid": LISTED_WHATSAPP_JID,
        "sender_jid": "852111111111@lid",
        "sender_phone": "85211111111",
        "push_name": "Someone",
        "text": text,
        "from_me": False,
        "timestamp_ms": 1_757_600_000_000,
        "quoted": quoted,
    }


# --- platform id stored on ingest --------------------------------------


async def test_whatsapp_platform_message_id_is_stored_on_ingest(tmp_path) -> None:
    config = _whatsapp_config()
    store = RecordingStore()
    observer = WhatsAppObserver(config, store, _sink(tmp_path))

    await observer.handle_event(_whatsapp_message("hi", event_id="3A6F8E77ECAA933F28C7"))

    assert len(store.inserted) == 1
    assert store.inserted[0][_PLATFORM_MESSAGE_ID] == "3A6F8E77ECAA933F28C7"


async def test_signal_platform_message_id_is_the_envelope_timestamp(tmp_path) -> None:
    config = _signal_config()
    store = RecordingStore()
    observer = SignalObserver(config, store, _sink(tmp_path))

    await observer.handle_event(_signal_envelope("hi"))

    assert len(store.inserted) == 1
    assert store.inserted[0][_PLATFORM_MESSAGE_ID] == "1757600000000"


# --- WhatsApp reply detection -------------------------------------------


async def test_whatsapp_quoted_reply_to_ora_phone_is_flagged(tmp_path) -> None:
    config = _whatsapp_config()
    store = RecordingStore()
    observer = WhatsAppObserver(config, store, _sink(tmp_path))

    await observer.handle_event(
        _whatsapp_message("yes", quoted={"sender_phone": OWN_WHATSAPP_PHONE, "id": "X1"})
    )

    assert store.inserted[0][_IS_REPLY_TO_ORA] is True


async def test_whatsapp_quoted_reply_to_ora_lid_is_flagged(tmp_path) -> None:
    config = _whatsapp_config()
    store = RecordingStore()
    observer = WhatsAppObserver(config, store, _sink(tmp_path))

    await observer.handle_event(
        _whatsapp_message("yes", quoted={"sender_jid": f"{OWN_WHATSAPP_LID}@lid", "id": "X1"})
    )

    assert store.inserted[0][_IS_REPLY_TO_ORA] is True


async def test_whatsapp_quoted_reply_to_someone_else_is_not_flagged(tmp_path) -> None:
    config = _whatsapp_config()
    store = RecordingStore()
    observer = WhatsAppObserver(config, store, _sink(tmp_path))

    await observer.handle_event(
        _whatsapp_message("yes", quoted={"sender_phone": "85299999999", "id": "X1"})
    )

    assert store.inserted[0][_IS_REPLY_TO_ORA] is False


async def test_whatsapp_message_with_no_quote_is_unaffected(tmp_path) -> None:
    config = _whatsapp_config()
    store = RecordingStore()
    observer = WhatsAppObserver(config, store, _sink(tmp_path))

    await observer.handle_event(_whatsapp_message("hello", quoted=None))

    assert store.inserted[0][_IS_REPLY_TO_ORA] is False
    assert store.inserted[0][_PLATFORM_MESSAGE_ID] == "3A6F8E77"


# --- Signal reply detection ----------------------------------------------


async def test_signal_quoted_reply_to_ora_is_flagged(tmp_path) -> None:
    config = _signal_config()
    store = RecordingStore()
    observer = SignalObserver(config, store, _sink(tmp_path))

    await observer.handle_event(
        _signal_envelope("yes", quote={"id": 1_757_599_000_000, "author": OWN_SIGNAL_ACCOUNT})
    )

    assert store.inserted[0][_IS_REPLY_TO_ORA] is True


async def test_signal_quoted_reply_to_someone_else_is_not_flagged(tmp_path) -> None:
    config = _signal_config()
    store = RecordingStore()
    observer = SignalObserver(config, store, _sink(tmp_path))

    await observer.handle_event(
        _signal_envelope("yes", quote={"id": 1_757_599_000_000, "author": "+85299999999"})
    )

    assert store.inserted[0][_IS_REPLY_TO_ORA] is False


async def test_signal_message_with_no_quote_is_unaffected(tmp_path) -> None:
    config = _signal_config()
    store = RecordingStore()
    observer = SignalObserver(config, store, _sink(tmp_path))

    await observer.handle_event(_signal_envelope("hello"))

    assert store.inserted[0][_IS_REPLY_TO_ORA] is False


def test_signal_parse_event_exposes_quoted_author_via_is_reply_to_ora() -> None:
    """Unit-level check directly on `parse_event`, not just end-to-end."""
    parsed = parse_signal_event(
        _signal_envelope(
            "yes", quote={"id": 1, "authorUuid": OWN_SIGNAL_ACCOUNT}
        ),
        own_account=OWN_SIGNAL_ACCOUNT,
    )
    assert parsed is not None
    assert parsed.is_reply_to_ora is True


def test_whatsapp_parse_event_exposes_platform_message_id() -> None:
    parsed = parse_whatsapp_event(_whatsapp_message("hi", event_id="ABC123"))
    assert parsed is not None
    assert parsed.platform_message_id == "ABC123"
