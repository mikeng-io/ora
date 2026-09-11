"""ORA-18: image capture in observe_signal.py. Fake `getAttachment`
transport, fake vision client, real plain storage under tmp_path — no
network, no Postgres (a RecordingStore fake stands in)."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from loop.config import Clocks, Config, Env, Room
from loop.logging import LogSink
from loop.observe_signal import AttachmentRef, SignalObserver, parse_event

OWN_ACCOUNT = "+85290000000"
LISTED_GROUP = "listed-group-id"

# A minimal valid PNG so media_store can write real bytes.
_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _solid_png_base64(rgb: tuple[int, int, int]) -> str:
    """A second, DIFFERENT valid PNG (distinct bytes -> distinct sha256),
    for tests that need two genuinely different images."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
    row = bytes(rgb) * 4
    raw = (b"\x00" + row) * 4
    idat = chunk(b"IDAT", zlib.compress(raw, 9))
    png = sig + ihdr + idat + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def _config() -> Config:
    return Config(
        env=Env(signal_account=OWN_ACCOUNT, signal_base_url="http://fake-signal"),
        clocks=Clocks(),
        workspaces={},
        rooms={("signal", LISTED_GROUP): Room("signal", LISTED_GROUP, "Signal")},
    )


def _sink(tmp_path) -> LogSink:
    return LogSink(json_path=tmp_path / "ora.log", console=Console(quiet=True))


def _image_event(text: str = "", *, att_id: str = "att-1", size: int = 68) -> dict:
    data: dict[str, Any] = {
        "message": text,
        "timestamp": 1_757_600_000_000,
        "groupInfo": {"groupId": LISTED_GROUP},
        "attachments": [
            {"id": att_id, "contentType": "image/png", "size": size, "filename": "photo.png"}
        ],
    }
    return {
        "envelope": {
            "source": "+85211111111",
            "sourceName": "Someone",
            "timestamp": 1_757_600_000_000,
            "dataMessage": data,
        }
    }


class RecordingStore:
    """A minimal store fake: media_objects as a dict, messages as a list."""

    def __init__(self) -> None:
        self.media_objects: dict[str, dict] = {}
        self.inserted_messages: list[dict] = []

    async def execute(self, query: str, *args: object) -> str:
        if "INSERT INTO media_objects" in query:
            sha256, media_type, size, path, created_at = args
            self.media_objects.setdefault(
                sha256,
                {
                    "sha256": sha256, "media_type": media_type, "size": size, "path": path,
                    "comprehended": False, "title": None, "description": None, "content": None,
                },
            )
        elif "UPDATE media_objects" in query:
            title, description, content, describe_model, describe_call_id, sha256 = args
            row = self.media_objects[sha256]
            row["describe_call_id"] = describe_call_id
            row.update(
                title=title, description=description, content=content,
                comprehended=True, describe_model=describe_model,
            )
        elif "INSERT INTO messages" in query:
            (
                platform, conversation_id, workspace, sender_id, person_id,
                ts, body, body_len, media_sha256,
            ) = args
            self.inserted_messages.append(
                {"platform": platform, "conversation_id": conversation_id, "body": body,
                 "media_sha256": media_sha256}
            )
        elif "INSERT INTO model_calls" in query:
            pass
        return "OK"

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        if "media_objects" in query:
            (sha256,) = args
            return self.media_objects.get(sha256)
        return None

    async def fetchval(self, query: str, *args: object) -> Any:
        return 1  # a fake model_calls id


@dataclass
class _FakeAttachmentSession:
    """Fakes aiohttp's ClientSession/post/json chain for getAttachment."""

    payload: dict

    async def __aenter__(self) -> _FakeAttachmentSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> _FakeAttachmentSession:
        return self

    @property
    def status(self) -> int:
        return 200

    async def json(self) -> dict:
        return self.payload


@dataclass
class _FakeVisionCompletions:
    body: str
    finish_reason: str = "stop"
    calls: list = field(default_factory=list)

    async def create(self, **kwargs: object) -> Any:
        self.calls.append(kwargs)
        return _fake_response(self.body, self.finish_reason)


def _fake_response(content: str, finish_reason: str) -> Any:
    @dataclass
    class Message:
        content: str | None

    @dataclass
    class Choice:
        message: Message
        finish_reason: str | None

    @dataclass
    class Response:
        choices: list

    return Response(choices=[Choice(Message(content), finish_reason)])


@dataclass
class _FakeVisionChat:
    completions: _FakeVisionCompletions


@dataclass
class _FakeVisionClient:
    completions: _FakeVisionCompletions

    def __post_init__(self) -> None:
        self.chat = _FakeVisionChat(completions=self.completions)


async def test_image_only_message_is_admitted_not_dropped() -> None:
    """An image with no caption text used to parse to None (empty body) —
    ORA-18 fixes that: an image attachment alone is enough to admit."""
    parsed = parse_event(_image_event(""), own_account=OWN_ACCOUNT)
    assert parsed is not None
    assert parsed.image == AttachmentRef(platform_id="att-1", media_type="image/png", size=68)


async def test_image_with_caption_keeps_both() -> None:
    parsed = parse_event(_image_event("look at this"), own_account=OWN_ACCOUNT)
    assert parsed is not None
    assert parsed.body == "look at this"
    assert parsed.image is not None


async def test_capture_stores_bytes_and_sets_media_sha256(tmp_path, monkeypatch) -> None:
    fake_session = _FakeAttachmentSession({"result": _PNG_BASE64})
    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: fake_session
    )

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(config, store, sink, media_root=tmp_path / "media")

    await observer.handle_event(_image_event("nice"))

    assert len(store.inserted_messages) == 1
    msg = store.inserted_messages[0]
    assert msg["media_sha256"] is not None
    assert "[image " in msg["body"]
    assert msg["body"].startswith("nice ")
    stored = store.media_objects[msg["media_sha256"]]
    assert stored["comprehended"] is False  # no vision_client wired


async def test_capture_with_vision_client_comprehends(tmp_path, monkeypatch) -> None:
    fake_session = _FakeAttachmentSession({"result": _PNG_BASE64})
    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: fake_session
    )

    vision_body = json.dumps({"title": "a photo", "description": "a test image", "content": ""})
    vision_client = _FakeVisionClient(completions=_FakeVisionCompletions(vision_body))

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(
        config, store, sink, media_root=tmp_path / "media", vision_client=vision_client
    )

    await observer.handle_event(_image_event(""))

    msg = store.inserted_messages[0]
    stored = store.media_objects[msg["media_sha256"]]
    assert stored["comprehended"] is True
    assert stored["title"] == "a photo"
    assert stored["description"] == "a test image"


async def test_fetch_failure_falls_back_to_bare_placeholder(tmp_path, monkeypatch) -> None:
    """`getAttachment` failing must never cost the message (R-5)."""

    class _FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def post(self, *a, **kw):
            return self

        status = 500

        async def json(self):
            return {}

    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: _FailingSession()
    )

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(config, store, sink, media_root=tmp_path / "media")

    await observer.handle_event(_image_event("hi"))

    msg = store.inserted_messages[0]
    assert msg["media_sha256"] is None
    assert msg["body"] == "hi [image]"


async def test_second_message_with_same_image_is_not_recomprehended(tmp_path, monkeypatch) -> None:
    """Content-addressing: the same sha256 arriving twice must not call
    vision a second time once comprehended."""
    fake_session = _FakeAttachmentSession({"result": _PNG_BASE64})
    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: fake_session
    )
    vision_body = json.dumps({"title": "t", "description": "d", "content": ""})
    completions = _FakeVisionCompletions(vision_body)
    vision_client = _FakeVisionClient(completions=completions)

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(
        config, store, sink, media_root=tmp_path / "media", vision_client=vision_client
    )

    await observer.handle_event(_image_event("first"))
    await observer.handle_event(_image_event("second"))

    assert len(completions.calls) == 1  # comprehended once, not twice


class _PerAttachmentSession:
    """`getAttachment`'s response depends on which attachment id was
    asked for — needed to fake TWO different images in one test."""

    def __init__(self, payload_by_id: dict[str, dict]) -> None:
        self._payload_by_id = payload_by_id
        self._next_payload: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def post(self, *args, **kwargs):
        body = kwargs.get("json") or {}
        att_id = (body.get("params") or {}).get("id", "")
        self._next_payload = self._payload_by_id.get(att_id, {})
        return self

    status = 200

    async def json(self):
        return self._next_payload


async def test_two_different_images_in_succession_each_get_their_own_row(
    tmp_path, monkeypatch
) -> None:
    """The busy-content-addressing test above proves the SAME image is
    never re-comprehended; this proves two DIFFERENT images do not
    collide with each other (distinct sha256, both comprehended)."""
    fake_session = _PerAttachmentSession(
        {
            "att-1": {"result": _PNG_BASE64},
            "att-2": {"result": _solid_png_base64((10, 20, 30))},
        }
    )
    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: fake_session
    )
    vision_body = json.dumps({"title": "t", "description": "d", "content": ""})
    completions = _FakeVisionCompletions(vision_body)
    vision_client = _FakeVisionClient(completions=completions)

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(
        config, store, sink, media_root=tmp_path / "media", vision_client=vision_client
    )

    await observer.handle_event(_image_event("first", att_id="att-1"))
    await observer.handle_event(_image_event("second", att_id="att-2"))

    assert len(completions.calls) == 2  # each image comprehended once
    shas = {m["media_sha256"] for m in store.inserted_messages}
    assert len(shas) == 2  # two distinct sha256, no collision


class _RaisingMediaStore:
    """Fakes `loop.media_store.store` raising — e.g. a full disk or a
    read-only ./state — to prove `_capture_image` degrades rather than
    losing the message (the review's blocker)."""

    def store(self, *args, **kwargs):
        raise OSError("No space left on device")


async def test_media_store_raising_falls_back_to_bare_placeholder(tmp_path, monkeypatch) -> None:
    fake_session = _FakeAttachmentSession({"result": _PNG_BASE64})
    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: fake_session
    )
    monkeypatch.setattr(
        "loop.observe_signal.media_store.store",
        _RaisingMediaStore().store,
    )

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(config, store, sink, media_root=tmp_path / "media")

    await observer.handle_event(_image_event("hi"))

    msg = store.inserted_messages[0]
    assert msg["media_sha256"] is None
    assert msg["body"] == "hi [image]"


async def test_vision_client_raising_falls_back_gracefully(tmp_path, monkeypatch) -> None:
    """Belt and braces: even though vision.comprehend() now fails closed
    internally, _capture_image's own try/except must also survive a
    raising vision client (defense in depth, not redundancy)."""
    fake_session = _FakeAttachmentSession({"result": _PNG_BASE64})
    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: fake_session
    )

    class _RaisingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("simulated vision outage")

    class _RaisingChat:
        completions = _RaisingCompletions()

    class _RaisingVisionClient:
        chat = _RaisingChat()

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(
        config, store, sink, media_root=tmp_path / "media",
        vision_client=_RaisingVisionClient(),
    )

    await observer.handle_event(_image_event("hi"))

    # The image itself still lands (custody succeeded); only comprehension
    # failed, and it failed silently rather than costing the message.
    msg = store.inserted_messages[0]
    assert msg["media_sha256"] is not None
    assert "[image " in msg["body"]


async def test_oversized_attachment_is_refused_before_fetching(tmp_path, monkeypatch) -> None:
    """`image.size` over the cap must skip the fetch entirely — no
    getAttachment call, no bytes in memory."""
    calls = []

    class _CountingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def post(self, *a, **kw):
            calls.append(1)
            return self

    monkeypatch.setattr(
        "loop.observe_signal.aiohttp.ClientSession", lambda **kw: _CountingSession()
    )

    config = _config()
    store = RecordingStore()
    sink = _sink(tmp_path)
    observer = SignalObserver(config, store, sink, media_root=tmp_path / "media")

    oversized = 9 * 1024 * 1024  # over the 8 MiB cap
    await observer.handle_event(_image_event("hi", size=oversized))

    assert calls == []  # never even tried to fetch
    msg = store.inserted_messages[0]
    assert msg["media_sha256"] is None
    assert msg["body"] == "hi [image]"
