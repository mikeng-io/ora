"""signal-cli SSE -> `messages` rows (images, ORA-18).

`SignalListener` is `reference/platform/signal/listener.py`, effectively
byte-for-byte (long-lived GET, full-jitter exponential backoff, one bad
event logged and skipped, a raising handler never killing the stream).

`parse_event` is a TRIMMED `reference/platform/signal/parser.py::parse_event`:
no reactions, no stickers, no mentions, no quotes — group text (and now
image attachments, ORA-18) only. Two rules kept because they are
load-bearing, not incidental (Opus audit finding 12): a `syncMessage` (no
`dataMessage`) parses to `None`, and `own_account`'s own send parses to
`None` even inside a listed room — the allowlist alone cannot catch that,
because the sync row's conversation_id IS the listed group.

`SignalObserver.handle_event` is the allowlist gate: parse, then check
`rooms.toml` — dropped before any store write, DROP logged once per room.
An image attachment is fetched over the SAME JSON-RPC endpoint `send`
already uses (`getAttachment`, `reference/platform/signal/media.py`'s shape —
the reference project's own docstring there admits the SUCCESS shape was never verified
live because no attachment ever arrived on that deployment; this is the
same unverified-until-proven shape, not a stronger claim).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from loop import media_store, vision
from loop.config import Config
from loop.logging import LogSink
from loop.media_store import MEDIA_SHORTID_LEN
from loop.people import PeopleDirectory
from loop.store import Store

log = logging.getLogger("ora.signal.listener")

_IMAGE_TYPES_ALLOWED_PREFIX = "image/"

# 8 MiB — the reference project's own measured ceiling for still images ([media] vision_max_bytes
# in config.example.toml, "chosen for images"; her video path needs a wider one
# because clips are routinely larger, but Ora has no video path). Checked
# against the platform's CLAIMED size before fetching, and against the real
# byte count after — a platform can misreport or omit `size`.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


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
class AttachmentRef:
    platform_id: str
    media_type: str
    size: int | None


@dataclass(frozen=True)
class ParsedMessage:
    platform: str
    conversation_id: str
    sender_id: str
    sender_name: str | None
    ts: datetime
    body: str
    image: AttachmentRef | None = None


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


def _first_image(data: dict) -> AttachmentRef | None:
    """The first `image/*` attachment, or `None` — stickers, voice notes,
    documents and every other attachment kind stay trimmed (ORA-18: images
    only). Only the first: a burst of photos is one caption's worth of
    attention in v1, not a gallery."""
    for att in data.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        media_type = str(att.get("contentType") or "")
        if not media_type.lower().startswith(_IMAGE_TYPES_ALLOWED_PREFIX):
            continue
        platform_id = str(att.get("id") or "")
        if not platform_id:
            continue
        size = att.get("size")
        return AttachmentRef(
            platform_id=platform_id,
            media_type=media_type,
            size=size if isinstance(size, int) else None,
        )
    return None


def parse_event(event: dict, own_account: str) -> ParsedMessage | None:
    """`None` for: an unparseable envelope, a `syncMessage`/typing/receipt
    (no `dataMessage`), a DM (no `groupInfo`), her own send (even inside a
    listed room — the sync row's `conversation_id` is the group id), or a
    row with neither text nor an image (a reaction/sticker/voice-note-only
    row, trimmed in v1)."""
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
        image = _first_image(data)
        if not body and image is None:
            return None  # reaction / sticker / voice-note-only — trimmed in v1

        ts_ms = int(env.get("timestamp") or data.get("timestamp") or 0)
        return ParsedMessage(
            platform="signal",
            conversation_id=group_id,
            sender_id=source,
            sender_name=env.get("sourceName") or None,
            ts=datetime.fromtimestamp(ts_ms / 1000, tz=UTC) if ts_ms else datetime.now(UTC),
            body=body,
            image=image,
        )
    except Exception:
        log.exception("unparseable envelope, skipping")
        return None


async def fetch_attachment(
    base_url: str, platform_id: str, *, timeout_seconds: float = 8.0
) -> bytes | None:
    """`getAttachment` over the same JSON-RPC endpoint `send` already uses
    (`reference/platform/signal/media.py`'s shape). `None` for anything that is
    not a real success — unreachable, an error envelope, or a payload this
    adapter cannot decode. UNVERIFIED against a real attachment (the reference project's
    own docstring: "no attachment has ever arrived on this deployment" —
    the success shape here is the same measured-not-proven claim)."""
    url = f"{base_url.rstrip('/')}/api/v1/rpc"
    payload = {
        "jsonrpc": "2.0",
        "method": "getAttachment",
        "params": {"id": platform_id},
        "id": "ora-getAttachment",
    }
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session,
            session.post(url, json=payload) as resp,
        ):
            if resp.status != 200:
                log.warning("getAttachment: HTTP %s from %s", resp.status, url)
                return None
            body = await resp.json()
    except Exception as exc:  # noqa: BLE001 — a media fetch failure is never fatal
        log.warning("getAttachment unreachable at %s: %s", url, type(exc).__name__)
        return None

    if not isinstance(body, dict) or body.get("error"):
        log.info("getAttachment: signal-cli did not produce %s", platform_id)
        return None
    return _decode_attachment(body.get("result"))


def _decode_attachment(result: Any) -> bytes | None:
    encoded: Any = None
    if isinstance(result, str):
        encoded = result
    elif isinstance(result, dict):
        for key in ("data", "base64", "attachment"):
            if isinstance(result.get(key), str):
                encoded = result[key]
                break
    if not isinstance(encoded, str) or not encoded:
        log.warning(
            "getAttachment returned a shape this adapter does not read: %s (keys only)",
            sorted(result) if isinstance(result, dict) else type(result).__name__,
        )
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        log.warning("getAttachment returned non-base64 content")
        return None


class SignalObserver:
    def __init__(
        self,
        config: Config,
        store: Store,
        sink: LogSink,
        people: PeopleDirectory | None = None,
        vision_client: Any | None = None,
        media_root: str | Path = "./state/media",
    ) -> None:
        self._config = config
        self._store = store
        self._sink = sink
        self._people = people or PeopleDirectory([])
        # `None` (the default) means images are stored but never comprehended
        # — a custody-only mode, still honest (an
        # uncomprehended row claims nothing, which is exactly true here).
        self._vision_client = vision_client
        self._media_root = media_root

    async def _capture_image(self, image: AttachmentRef, conversation_id: str) -> str | None:
        """Fetch, store, and (best-effort) comprehend one image. Returns the
        sha256 on success or `None` on any failure — a media failure must
        never cost the message (R-5's discipline). The whole body is one
        try/except: `fetch_attachment` was already fail-closed on its own,
        but `media_store.store` can raise (ENOSPC, a read-only ./state),
        `vision.comprehend` can raise anything but a timeout (a real
        `AsyncOpenAI` client raises `APIConnectionError`/`RateLimitError`/
        `APIStatusError`, and an empty `choices` list raises `IndexError`),
        and both store calls can raise — none of those may propagate into
        `handle_event` and cost the row (caught in review, Opus)."""
        if image.size is not None and image.size > MAX_IMAGE_BYTES:
            log.warning("image attachment too large (%s bytes), refusing", image.size)
            return None
        try:
            data = await fetch_attachment(self._config.env.signal_base_url, image.platform_id)
            if data is None:
                return None
            if len(data) > MAX_IMAGE_BYTES:
                log.warning("fetched image exceeds the size cap (%s bytes), refusing", len(data))
                return None
            stored = media_store.store(data, media_type=image.media_type, root=self._media_root)
            existing = await self._store.fetchrow(
                "SELECT comprehended FROM media_objects WHERE sha256 = $1", stored.sha256
            )
            if existing is None:
                await self._store.execute(
                    """INSERT INTO media_objects (sha256, media_type, size, path, created_at)
                       VALUES ($1,$2,$3,$4,$5)
                       ON CONFLICT (sha256) DO NOTHING""",
                    stored.sha256, image.media_type, stored.size, stored.path,
                    datetime.now(UTC),
                )
            already_comprehended = bool(existing and existing["comprehended"])
            if not already_comprehended and self._vision_client is not None:
                result = await vision.comprehend(
                    self._vision_client, data=data, media_type=image.media_type,
                    store=self._store, platform="signal", conversation_id=conversation_id,
                )
                if result.ok:
                    await self._store.execute(
                        """UPDATE media_objects
                           SET title = $1, description = $2, content = $3,
                               comprehended = TRUE, describe_model = $4, describe_call_id = $5
                           WHERE sha256 = $6""",
                        result.title, result.description, result.content,
                        vision.VISION_MODEL, result.call_id, stored.sha256,
                    )
            return stored.sha256
        except Exception:  # noqa: BLE001 — a media failure must never cost the message
            log.exception("image capture failed; falling back to a bare placeholder")
            return None

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

        media_sha256: str | None = None
        body = parsed.body
        if parsed.image is not None:
            media_sha256 = await self._capture_image(parsed.image, parsed.conversation_id)
            placeholder = (
                f"[image {media_sha256[:MEDIA_SHORTID_LEN]}]" if media_sha256 else "[image]"
            )
            body = f"{body} {placeholder}".strip() if body else placeholder

        await self._store.execute(
            """INSERT INTO messages
               (platform, conversation_id, workspace, sender_id, person_id,
                is_ora, ts, body, body_len, media_sha256)
               VALUES ($1,$2,$3,$4,$5,FALSE,$6,$7,$8,$9)""",
            "signal",
            parsed.conversation_id,
            self._config.workspace,
            parsed.sender_id,
            person.id if person else None,
            parsed.ts,
            body,
            len(body),
            media_sha256,
        )
        self._sink.event(
            "OBSERVE",
            room.label,
            f"← {person.name if person else parsed.sender_name or 'unknown'}: {body}",
        )

    async def run(self) -> None:
        listener = SignalListener(self._config.env.signal_base_url)
        await listener.listen(self.handle_event)
