"""whatsapp-bridge SSE -> `messages` rows.

`WhatsAppListener` is `reference/platform/whatsapp/listener.py`, effectively
byte-for-byte (long-lived GET, full-jitter exponential backoff, heartbeat/
dropped handled at listener level and never reaching the parser).

`parse_event` is a TRIMMED `reference/platform/whatsapp/parser.py::parse_event`:
no media, no reactions, no mentions, no quotes — group text only, and no
JID-form disambiguation (`reference/platform/whatsapp/jid.py`'s phone/lid forms)
since Ora resolves people from the raw `sender_jid` `people.toml` carries,
not a normalized form. `from_me` is dropped at parse time (R-12's first
pass) — load-bearing even inside a listed room, same as Signal's own-send
rule (Opus audit finding 12).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import aiohttp

from loop.config import Config
from loop.logging import LogSink
from loop.people import PeopleDirectory
from loop.store import Store

log = logging.getLogger("ora.whatsapp.listener")


class WhatsAppListener:
    def __init__(
        self,
        base_url: str,
        *,
        max_backoff: float = 60.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/events"
        self._max_backoff = max_backoff
        self._sleep = sleeper
        self._clock = clock
        self.last_event_at: float | None = None
        self.connected = False

    def _next_backoff(self, attempt: int) -> float:
        return random.uniform(0.0, min(self._max_backoff, 1.0 * (2**attempt)))  # noqa: S311

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        attempt = 0
        while True:
            try:
                await self._stream(on_event)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect, always
                log.warning("SSE connection failed (%s); reconnecting", type(exc).__name__)
            finally:
                self.connected = False
            delay = self._next_backoff(attempt)
            attempt = min(attempt + 1, 6)
            log.info("reconnecting to whatsapp-bridge in %.1fs", delay)
            await self._sleep(delay)

    async def _stream(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(self._url, headers={"Accept": "text/event-stream"}) as resp,
        ):
            resp.raise_for_status()
            self.connected = True
            log.info("SSE connected to %s", self._url)
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    log.warning("skipping unparseable SSE line")
                    continue
                self.last_event_at = self._clock()
                event_type = event.get("type") if isinstance(event, dict) else None
                if event_type == "heartbeat":
                    continue
                if event_type == "dropped":
                    log.warning(
                        "bridge dropped inbound events while no SSE client was "
                        "connected (dropped_total=%s) — audit rows were lost upstream",
                        event.get("dropped_total"),
                    )
                    continue
                try:
                    await on_event(event)
                except Exception:
                    log.exception("event handler raised; continuing")


@dataclass(frozen=True)
class ParsedMessage:
    platform: str
    conversation_id: str
    sender_id: str
    sender_name: str | None
    ts: datetime
    body: str


def parse_event(event: dict) -> ParsedMessage | None:
    """`None` for: a non-`message` event, `from_me` (never ingest our own
    sends, even inside a listed room), a broadcast/newsletter feed, or a
    turn with no text (media-only, trimmed in v1)."""
    try:
        if not isinstance(event, dict) or event.get("type") != "message":
            return None
        if event.get("from_me"):
            return None  # R-12's first pass
        chat_jid = event.get("chat_jid")
        if not isinstance(chat_jid, str) or not chat_jid:
            return None
        if chat_jid.endswith("@broadcast") or "newsletter" in chat_jid:
            return None  # a feed, not a room

        text = str(event.get("text") or "").strip()
        if not text:
            return None  # media-only — trimmed in v1

        ts_ms = event.get("timestamp_ms")
        ts = (
            datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            if isinstance(ts_ms, int) and not isinstance(ts_ms, bool) and ts_ms
            else datetime.now(UTC)
        )
        return ParsedMessage(
            platform="whatsapp",
            conversation_id=chat_jid,
            sender_id=str(event.get("sender_jid") or event.get("sender_phone") or ""),
            sender_name=event.get("push_name") or None,
            ts=ts,
            body=text,
        )
    except Exception:
        log.exception("unparseable whatsapp event, skipping")
        return None


class WhatsAppObserver:
    def __init__(
        self,
        config: Config,
        store: Store,
        sink: LogSink,
        people: PeopleDirectory | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._sink = sink
        self._people = people or PeopleDirectory([])

    async def handle_event(self, event: dict) -> None:
        parsed = parse_event(event)
        if parsed is None:
            return
        room = self._config.room_for("whatsapp", parsed.conversation_id)
        if room is None:
            self._sink.event(
                "DROP",
                "whatsapp",
                "not in rooms.toml",
                platform="whatsapp",
                conversation_id=parsed.conversation_id,
            )
            return
        person = self._people.resolve("whatsapp", parsed.sender_id)
        await self._store.execute(
            """INSERT INTO messages
               (platform, conversation_id, workspace, sender_id, person_id,
                is_ora, ts, body, body_len)
               VALUES ($1,$2,$3,$4,$5,FALSE,$6,$7,$8)""",
            "whatsapp",
            parsed.conversation_id,
            self._config.workspace,
            parsed.sender_id,
            person.id if person else None,
            parsed.ts,
            parsed.body,
            len(parsed.body),
        )
        self._sink.event(
            "OBSERVE",
            room.label,
            f"← {person.name if person else parsed.sender_name or 'unknown'}: "
            f"{parsed.body}",
        )

    async def run(self) -> None:
        listener = WhatsAppListener(self._config.env.whatsapp_base_url)
        await listener.listen(self.handle_event)
