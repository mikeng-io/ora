"""The stage broadcaster: publishes each line Ora decides to speak over
Server-Sent Events so a browser-side Live2D avatar can react on stage. Not
wired into `loop.py` — the session owner constructs one `Presenter`, calls
`await presenter.start()` once at startup, and `await presenter.speak(...)`
alongside each real send.

Fail-closed convention, matching `loop/search.py` and `loop/vision.py`:
`speak()` and `start()` never raise into the caller. The single property
that matters is that a browser tab not being open — or not existing at
all — must never cost the agent's real send into Signal/WhatsApp. A
publish with no subscribers is a no-op; a slow or dead subscriber is
dropped, never awaited on; a server that fails to bind leaves the
presenter silently inert rather than crashing the loop.

SSE, not WebSockets, mirrors the house pattern already used for inbound
events (`loop/observe_whatsapp.py`'s `WhatsAppListener`) — a long-lived GET,
`data: <json>\\n\\n` lines, one process, no extra dependency (aiohttp is
already in `pyproject.toml`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

log = logging.getLogger("ora.present")

# Bounded so one stuck browser tab cannot grow memory without limit; a full
# queue means that subscriber is dropped, never that the publisher waits.
_QUEUE_MAXSIZE = 16

# `web/` lives next to `loop/` at the repo root — served as static files so
# the same aiohttp process that answers /events can also serve index.html.
_WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


@dataclass(frozen=True)
class Line:
    """One spoken turn, as broadcast to subscribers."""

    text: str
    room: str
    platform: str
    ts: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        payload = json.dumps(
            {"text": self.text, "room": self.room, "platform": self.platform, "ts": self.ts}
        )
        return f"data: {payload}\n\n"


class Presenter:
    """Broadcasts spoken lines to any number of SSE subscribers (browser
    tabs showing the avatar). Owns no state about *whether* Ora should
    speak — that is `loop.py`'s decision; this module only makes the
    decision visible once it has already been made."""

    def __init__(self, *, host: str = "0.0.0.0", port: int = 8765) -> None:
        self._host = host
        self._port = port
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._runner: web.AppRunner | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[str]:
        """Register one subscriber queue. Used by the SSE handler, and
        directly by tests that exercise the broadcaster without an HTTP
        round trip."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    async def speak(self, text: str, *, room: str, platform: str) -> None:
        """Publish one line to every current subscriber. Never blocks on a
        subscriber and never raises into the caller — match
        `search.py`/`vision.py`'s convention: a browser page not being open
        must never cost the real send into the group."""
        try:
            payload = Line(text=text, room=room, platform=platform).to_sse()
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    log.warning(
                        "presenter subscriber is not draining; dropping it, not the line"
                    )
                    self._subscribers.discard(queue)
        except Exception:  # noqa: BLE001 — never raises into the caller
            log.exception("presenter speak failed; the real send is unaffected")

    async def start(self) -> bool:
        """Bind the aiohttp server and start serving `web/` + `/events`.
        Returns True on success, False on any failure (port already bound,
        no permission, `web/` missing, anything else) — the agent keeps
        running with no visible stage either way."""
        try:
            app = web.Application()
            app.router.add_get("/events", self._handle_events)
            if _WEB_ROOT.is_dir():
                # aiohttp's static resource has no automatic index.html
                # serving (unlike a directory listing, which would shadow
                # it) — so GET / is routed explicitly, and add_static
                # covers any other file web/index.html references.
                app.router.add_get("/", self._handle_index)
                app.router.add_static("/", _WEB_ROOT)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
            self._runner = runner
            log.info("presenter listening on http://%s:%s", self._host, self._port)
            return True
        except Exception:
            log.exception("presenter failed to start; continuing without a stage view")
            return False

    async def stop(self) -> None:
        """Best-effort teardown; never raises."""
        if self._runner is None:
            return
        try:
            await self._runner.cleanup()
        except Exception:
            log.exception("presenter failed to stop cleanly")
        finally:
            self._runner = None

    async def _handle_index(self, request: web.Request) -> web.StreamResponse:
        index_path = _WEB_ROOT / "index.html"
        if not index_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(index_path)

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # the page may be opened as a plain file:// tab (null
                # origin) rather than served — CORS must not be the reason
                # the avatar never moves.
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)
        queue = self.subscribe()
        try:
            while True:
                payload = await queue.get()
                await response.write(payload.encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("presenter SSE stream ended abnormally")
        finally:
            self.unsubscribe(queue)
        return response
