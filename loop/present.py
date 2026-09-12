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
from urllib.parse import urlsplit

from aiohttp import web

log = logging.getLogger("ora.present")

# Bounded so one stuck browser tab cannot grow memory without limit; a full
# queue means that subscriber is dropped, never that the publisher waits.
_QUEUE_MAXSIZE = 16

# `web/` lives next to `loop/` at the repo root — served as static files so
# the same aiohttp process that answers /events can also serve index.html.
_WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

# CORS allowlist for /events (security review finding, fixed here): the
# stage page is legitimately opened two ways — served same-origin by this
# process, or double-clicked open as a `file://` tab (which sends
# `Origin: null`). Neither needs a wildcard. The previous
# `Access-Control-Allow-Origin: "*"` let *any* website's script — a stray
# tab on the venue wifi, an ad frame, anything — `fetch()` the live SSE
# stream and read every line Ora ever decided to speak into the private
# Signal/WhatsApp groups. Only these origins are reflected back; anything
# else gets no CORS header at all, so the browser's normal same-origin
# policy blocks cross-site script reads (the stream still flows to the
# requesting socket — CORS is a browser-side read guard, not network
# access control, which is why the host default below matters too).
_ALLOWED_ORIGIN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_allowed_origin(origin: str) -> bool:
    if origin == "null":  # file:// tabs
        return True
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in _ALLOWED_ORIGIN_HOSTS


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

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        # Security review finding, fixed here: binding to 0.0.0.0 put the
        # unauthenticated /events stream — the live transcript of every
        # line Ora speaks into the private Signal/WhatsApp groups — on
        # every interface, reachable by any device on the venue wifi, not
        # just this laptop. Loopback is the safe default; a caller who
        # deliberately wants the LAN (e.g. a phone on the same stage) can
        # still pass host="0.0.0.0" explicitly.
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

    def _build_app(self) -> web.Application:
        """Construct the aiohttp app. Split out from `start()` so tests can
        exercise routes (e.g. the CORS headers on /events) via aiohttp's
        test client without binding a real socket."""
        app = web.Application()
        app.router.add_get("/events", self._handle_events)
        if _WEB_ROOT.is_dir():
            # aiohttp's static resource has no automatic index.html
            # serving (unlike a directory listing, which would shadow
            # it) — so GET / is routed explicitly, and add_static
            # covers any other file web/index.html references.
            app.router.add_get("/", self._handle_index)
            app.router.add_static("/", _WEB_ROOT)
        return app

    async def start(self) -> bool:
        """Bind the aiohttp server and start serving `web/` + `/events`.
        Returns True on success, False on any failure (port already bound,
        no permission, `web/` missing, anything else) — the agent keeps
        running with no visible stage either way."""
        try:
            app = self._build_app()
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
        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        origin = request.headers.get("Origin")
        if origin is not None and _is_allowed_origin(origin):
            # Reflect only an allowed origin, never "*" — see
            # _is_allowed_origin's docstring-comment above for why.
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        response = web.StreamResponse(status=200, headers=headers)
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
