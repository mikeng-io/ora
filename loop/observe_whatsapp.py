"""whatsapp-bridge SSE -> `messages` rows (images, parity with ORA-18).

`WhatsAppListener` is `reference/platform/whatsapp/listener.py`, effectively
byte-for-byte (long-lived GET, full-jitter exponential backoff, heartbeat/
dropped handled at listener level and never reaching the parser).

`parse_event` is a TRIMMED `reference/platform/whatsapp/parser.py::parse_event`:
no reactions, no mentions, no quotes — group text (and now image attachments)
only, and no JID-form disambiguation (`reference/platform/whatsapp/jid.py`'s
phone/lid forms) since Ora resolves people from the raw `sender_jid`
`people.toml` carries, not a normalized form. `from_me` is dropped at parse
time (R-12's first pass) — load-bearing even inside a listed room, same as
Signal's own-send rule (Opus audit finding 12).

**The `media` element's shape is UNVERIFIED.** The bridge's documented event
shape (measured live, 2026-09-12) shows only an empty `"media": []` — no
image has actually arrived on this deployment yet, so the shape of one
element inside that array is inferred, not measured, exactly the same
honesty `observe_signal.py`'s `fetch_attachment` docstring carries for
`getAttachment`'s success shape. This adapter reads, defensively, whichever
of these keys a given element happens to have: `mimetype`/`mime_type` (the
MIME type), `size`/`file_length` (the claimed byte count), `id`/`media_key`
(an opaque platform reference, kept only for logging — no code path uses it
to fetch anything), `url` (kept for the same reason), and an inline
`data` field carrying base64 bytes, if the bridge ever sends one. A `type`/
`kind` field naming something other than an image (`"video"`, `"audio"`,
`"document"`, `"sticker"`, `"ptt"`) is honoured when present, to keep the
image-only rule even if a non-image element happens to reuse an
`image/*` mimetype (a sticker frequently does).

**There is no documented media-download route.** The bridge's known routes
are `GET /health`, `GET /events`, `GET /self`, `POST /send`,
`POST /send-media`, `POST /edit`, `POST /react` — nothing fetches a
message's media by id or url. Signal's `getAttachment` has a real RPC
endpoint to call even though its *response* shape is unverified; WhatsApp
has no endpoint at all. Inventing one would be worse than admitting the
gap, so this adapter captures an image ONLY when the event already carries
it inline (a base64 `data` field) — any other shape (an `id`/`url` with no
inline bytes) degrades honestly: the image is not captured, the message
still lands with the bare `[image]` placeholder. This is a deliberate,
documented divergence from Signal, not an oversight.
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

log = logging.getLogger("ora.whatsapp.listener")

_IMAGE_TYPES_ALLOWED_PREFIX = "image/"
_NON_IMAGE_KINDS = {"video", "audio", "document", "sticker", "ptt", "voice"}

# 8 MiB — parity with Signal's MAX_IMAGE_BYTES (observe_signal.py). Defined
# locally rather than imported so this module never depends on another
# in-flight file's shape; the two are kept in sync by review, not by import.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


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
class AttachmentRef:
    """One inferred `media[]` element — see the module docstring for exactly
    which keys are read and which are UNVERIFIED. `platform_id` and `url`
    are carried only for logging/provenance; no fetch path uses them (there
    is no download route). `data_b64`, when present, is the only way this
    adapter ever gets real bytes."""

    media_type: str
    size: int | None
    platform_id: str | None
    url: str | None
    data_b64: str | None


def _first_image(media: list) -> AttachmentRef | None:
    """The first image-shaped element of `media`, or `None` — video, audio,
    documents and stickers stay trimmed (images only). Only the first: a
    burst of photos is one caption's worth of attention in v1, not a
    gallery (mirrors observe_signal.py's `_first_image`)."""
    for att in media or []:
        if not isinstance(att, dict):
            continue
        kind = str(att.get("type") or att.get("kind") or "").lower()
        if kind in _NON_IMAGE_KINDS:
            continue
        media_type = str(att.get("mimetype") or att.get("mime_type") or "")
        if not media_type.lower().startswith(_IMAGE_TYPES_ALLOWED_PREFIX):
            continue
        size = att.get("size")
        if size is None:
            size = att.get("file_length")
        platform_id = att.get("id") or att.get("media_key")
        data_b64 = att.get("data")
        return AttachmentRef(
            media_type=media_type,
            size=size if isinstance(size, int) else None,
            platform_id=str(platform_id) if platform_id else None,
            url=str(att.get("url")) if att.get("url") else None,
            data_b64=str(data_b64) if isinstance(data_b64, str) and data_b64 else None,
        )
    return None


@dataclass(frozen=True)
class ParsedMessage:
    platform: str
    conversation_id: str
    sender_id: str
    sender_name: str | None
    ts: datetime
    body: str
    image: AttachmentRef | None = None


def parse_event(event: dict) -> ParsedMessage | None:
    """`None` for: a non-`message` event, `from_me` (never ingest our own
    sends, even inside a listed room), a broadcast/newsletter feed, or a
    turn with neither text nor an image (a reaction/other-media-only row,
    trimmed in v1). An image with no caption is enough to admit, mirroring
    how Signal's `parse_event` treats an image-only `dataMessage`."""
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
        image = _first_image(event.get("media") or [])
        if not text and image is None:
            return None  # reaction / other-media-only — trimmed in v1

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
            image=image,
        )
    except Exception:
        log.exception("unparseable whatsapp event, skipping")
        return None


def _decode_inline_media(image: AttachmentRef) -> bytes | None:
    """The only way this adapter ever gets real image bytes: an inline
    base64 `data` field on the media element. `None` for anything else —
    no `id`/`url` alone is fetchable (no documented download route; see the
    module docstring) — and for a `data` field that fails to decode."""
    if not image.data_b64:
        log.info("whatsapp media has no inline data and no known fetch route; refusing")
        return None
    try:
        return base64.b64decode(image.data_b64, validate=True)
    except (binascii.Error, ValueError):
        log.warning("whatsapp media inline `data` was not valid base64")
        return None


class WhatsAppObserver:
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
        # — a custody-only mode, still honest (an uncomprehended row claims
        # nothing, which is exactly true here). Same default as Signal.
        self._vision_client = vision_client
        self._media_root = media_root

    async def _capture_image(self, image: AttachmentRef, conversation_id: str) -> str | None:
        """Decode (if inline bytes exist), store, and (best-effort)
        comprehend one image. Returns the sha256 on success or `None` on
        any failure — a media failure must never cost the message (R-5's
        discipline, same as observe_signal.py's `_capture_image`). The
        whole body is one try/except: `_decode_inline_media` is already
        fail-closed on its own, but `media_store.store` can raise (ENOSPC, a
        read-only ./state), `vision.comprehend` can raise anything but a
        timeout, and both store calls can raise — none of those may
        propagate into `handle_event` and cost the row."""
        if image.size is not None and image.size > MAX_IMAGE_BYTES:
            log.warning("image attachment too large (%s bytes), refusing", image.size)
            return None
        try:
            data = _decode_inline_media(image)
            if data is None:
                return None
            if len(data) > MAX_IMAGE_BYTES:
                log.warning("decoded image exceeds the size cap (%s bytes), refusing", len(data))
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
                    store=self._store, platform="whatsapp", conversation_id=conversation_id,
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
            "whatsapp",
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
            f"← {person.name if person else parsed.sender_name or 'unknown'}: "
            f"{body}",
        )

    async def run(self) -> None:
        listener = WhatsAppListener(self._config.env.whatsapp_base_url)
        await listener.listen(self.handle_event)
