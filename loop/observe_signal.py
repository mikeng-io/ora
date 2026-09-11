"""signal-cli SSE -> `messages` rows (design/07 item 9).

`SignalListener` is `nora/platform/signal/listener.py`, effectively
byte-for-byte (long-lived GET, full-jitter exponential backoff, one bad
event logged and skipped, a raising handler never killing the stream).

`parse_event` is a TRIMMED `nora/platform/signal/parser.py::parse_event`:
no media, no reactions, no stickers, no mentions, no quotes — group text
only. Two rules kept because they are load-bearing, not incidental
(Opus audit finding 12): a `syncMessage` (no `dataMessage`) parses to
`None`, and `own_account`'s own send parses to `None` even inside a listed
room — the allowlist alone cannot catch that, because the sync row's
conversation_id IS the listed group.

`SignalObserver.handle_event` is the allowlist gate: parse, then check
`rooms.toml` — dropped before any store write, DROP logged once per room.
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

log = logging.getLogger("ora.signal.listener")


class SignalListener:
    def __init__(
        self,
        base_url: str,
        *,
        max_backoff: float = 60.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/events"
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
            log.info("reconnecting to signal-cli in %.1fs", delay)
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


def _extract_envelope(event: dict) -> dict | None:
    if "envelope" in event:
        return event["envelope"] if isinstance(event["envelope"], dict) else None
    params = event.get("params")
    if isinstance(params, dict):
        result = params.get("result")
        if isinstance(result, dict):
            return result.get("envelope") or {}
        return params.get("envelope") or {}
    return None


def _data_message(env: dict) -> dict | None:
    data = env.get("dataMessage")
    if isinstance(data, dict):
        return data
    edit = env.get("editMessage")
    data = edit.get("dataMessage") if isinstance(edit, dict) else None
    return data if isinstance(data, dict) else None


def parse_event(event: dict, own_account: str) -> ParsedMessage | None:
    """`None` for: an unparseable envelope, a `syncMessage`/typing/receipt
    (no `dataMessage`), a DM (no `groupInfo`), her own send (even inside a
    listed room — the sync row's `conversation_id` is the group id), or an
    empty body (a reaction/sticker/media-only row, trimmed in v1)."""
    try:
        env = _extract_envelope(event or {})
        if env is None:
            return None
        data = _data_message(env)
        if data is None:
            return None  # syncMessage / typing / receipt / malformed

        group_id = (data.get("groupInfo") or {}).get("groupId")
        if not group_id:
            return None  # DM — out of scope

        source = env.get("source") or env.get("sourceNumber") or env.get("sourceUuid") or ""
        if own_account and source == own_account:
            return None  # never ingest our own sends (Opus audit finding 12)

        body = str(data.get("message") or "").strip()
        if not body:
            return None  # reaction / sticker / media-only — trimmed in v1

        ts_ms = int(env.get("timestamp") or data.get("timestamp") or 0)
        return ParsedMessage(
            platform="signal",
            conversation_id=group_id,
            sender_id=source,
            sender_name=env.get("sourceName") or None,
            ts=datetime.fromtimestamp(ts_ms / 1000, tz=UTC) if ts_ms else datetime.now(UTC),
            body=body,
        )
    except Exception:
        log.exception("unparseable envelope, skipping")
        return None


class SignalObserver:
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
        parsed = parse_event(event, own_account=self._config.env.signal_account)
        if parsed is None:
            return
        room = self._config.room_for("signal", parsed.conversation_id)
        if room is None:
            self._sink.event(
                "DROP",
                "signal",
                "not in rooms.toml",
                platform="signal",
                conversation_id=parsed.conversation_id,
            )
            return
        person = self._people.resolve("signal", parsed.sender_id)
        await self._store.execute(
            """INSERT INTO messages
               (platform, conversation_id, workspace, sender_id, person_id,
                is_ora, ts, body, body_len)
               VALUES ($1,$2,$3,$4,$5,FALSE,$6,$7,$8)""",
            "signal",
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
        listener = SignalListener(self._config.env.signal_base_url)
        await listener.listen(self.handle_event)
