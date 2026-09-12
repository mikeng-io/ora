"""Honcho client: feed / peer_card / representation / self-card read
(design/07 item 12). Shape from `reference/infra/honcho/client.py`, trimmed to
one workspace (no `WorkspaceRouter` — Ora has exactly one) and the four
verbs Ora uses: `feed` at Observe, `peer_card` + `representation` (the
self-card read is `representation(peer_id=ai_peer)` with no `target`) on
the tag turn.

Honcho is never load-bearing here either: every method logs and returns a
benign value (`False`, `[]`, an unreachable `Recollection`) on any
failure — an outage must not block a reply.

Stamp-stripping kept verbatim (`_STAMP`/`_ID`/`_PATTERN`/`_CONTRADICTION`):
Honcho's batch-level timestamp is not a recency signal at conclusion
granularity (the reference project's measured finding — 102 documents, six stamps, one
shared by 77), and rendering it turns a one-time request into what reads
as a standing directive (`render.render_self_card`'s hedge is the other
half of this fix).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, cast

import aiohttp

log = logging.getLogger("ora.honcho")

_STAMP = re.compile(r"^\[[^\]]*\]\s*")
_ID = re.compile(r"^\[id:[^\]]*\]\s*")
_PATTERN = re.compile(r"^\*\*Pattern\*\*\s*\[[^\]]*\]:\s*")
_CONTRADICTION = re.compile(r"^\*\*CONTRADICTION\*\*:\s*")

_MAX_DISTANCE = 0.5

_SESSION_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")
_B64_TO_URLSAFE = str.maketrans({"+": "-", "/": "_", "=": ""})


def _safe_conversation_key(conversation_id: str) -> str:
    """A Honcho-legal session key — a Signal group id's base64 (`+`, `/`,
    `=`) 422s the session-create endpoint otherwise."""
    candidate = conversation_id.translate(_B64_TO_URLSAFE)
    if candidate and _SESSION_ID_OK.match(candidate):
        return candidate
    return hashlib.sha256(conversation_id.encode()).hexdigest()


def _conclusions(representation: Any) -> tuple[str, ...]:
    """One markdown blob -> one conclusion per observation, never one per
    line (premises/sources/sub-labels are indented or bulleted and
    dropped)."""
    if not isinstance(representation, str):
        return ()
    out: list[str] = []
    for raw in representation.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("- ", "* ")) or line in ("-", "*"):
            continue
        if raw.startswith("   "):
            continue
        line = _PATTERN.sub("", _STAMP.sub("", _ID.sub("", line)), count=1)
        line = _CONTRADICTION.sub("", line, count=1).strip()
        if not line or line.startswith("**"):
            continue
        out.append(line)
    return tuple(out)


@dataclass(frozen=True)
class Recollection:
    reachable: bool
    conclusions: tuple[str, ...] = ()


@dataclass
class HonchoClient:
    base_url: str
    workspace_id: str
    ai_peer: str = "ora"
    feed_timeout: float = 2.0
    card_timeout: float = 3.0
    representation_timeout: float = 3.0

    _sessions: dict[str, str] = field(default_factory=dict, init=False)
    _configured_peers: set[tuple[str, str]] = field(default_factory=set, init=False)
    _known_peers: set[str] = field(default_factory=set, init=False)

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/v3/workspaces/{self.workspace_id}{path}"

    async def _post(
        self, url: str, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any] | None:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.post(url, json=payload) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho POST %s -> %s", url, r.status)
                    return None
                return cast(dict[str, Any], await r.json())
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho POST %s failed: %s", url, e)
            return None

    async def _get(self, url: str, timeout: float) -> dict[str, Any] | None:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.get(url) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho GET %s -> %s", url, r.status)
                    return None
                return cast(dict[str, Any], await r.json())
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho GET %s failed: %s", url, e)
            return None

    async def ensure_workspace(self) -> bool:
        """`POST /v3/workspaces` is idempotent (the live instance answers
        200 with the same id on a repeat POST) — call it once at startup so
        a fresh workspace (`ora-verify` tonight, `ora` on the day) exists
        before anything else calls into it."""
        result = await self._post(
            f"{self.base_url.rstrip('/')}/v3/workspaces",
            {"id": self.workspace_id},
            self.card_timeout,
        )
        return result is not None

    async def _create_peer(self, peer_id: str) -> None:
        if peer_id in self._known_peers:
            return
        result = await self._post(self._url("/peers"), {"id": peer_id}, self.card_timeout)
        if result is not None:
            self._known_peers.add(peer_id)

    async def _configure_peer(
        self, session_id: str, peer_id: str, *, observe_others: bool, observe_me: bool
    ) -> None:
        pair = (session_id, peer_id)
        if pair in self._configured_peers:
            return
        result = await self._post(
            self._url(f"/sessions/{session_id}/peers"),
            {peer_id: {"observe_others": observe_others, "observe_me": observe_me}},
            self.feed_timeout,
        )
        if result is not None:
            self._configured_peers.add(pair)

    async def ensure_session(self, conversation_id: str) -> str | None:
        """One session per room, holding every speaker as its own peer.
        Idempotent: the live instance answers 200 with the same id on a
        repeat POST."""
        cached = self._sessions.get(conversation_id)
        if cached is not None:
            await self._configure_peer(cached, self.ai_peer, observe_others=True, observe_me=True)
            return cached
        session_id = f"ora-conv-{_safe_conversation_key(conversation_id)}"
        result = await self._post(self._url("/sessions"), {"id": session_id}, self.feed_timeout)
        if result is None:
            return None
        returned = result.get("id")
        if not isinstance(returned, str) or not returned:
            return None
        self._sessions[conversation_id] = returned
        await self._configure_peer(returned, self.ai_peer, observe_others=True, observe_me=True)
        return returned

    async def feed(self, *, peer_id: str, text: str, conversation_id: str) -> bool:
        """Feed one message under its speaker's `peer_id`. Best-effort:
        `False` on any failure, the caller counts it and moves on."""
        session_id = await self.ensure_session(conversation_id)
        if session_id is None:
            return False
        if peer_id != self.ai_peer:
            await self._create_peer(peer_id)
            await self._configure_peer(
                session_id, peer_id, observe_others=False, observe_me=True
            )
        result = await self._post(
            self._url(f"/sessions/{session_id}/messages"),
            {"messages": [{"content": text, "peer_id": peer_id}]},
            self.feed_timeout,
        )
        return result is not None

    async def peer_card(self, *, peer_id: str) -> list[str]:
        """Durable recall about a peer. `[]` on any failure — a missing
        card is normal. `GET /peers/{id}/card` -> `{"peer_card": [...] |
        null}` (the reference project's own live-verified shape — this read `content` as a
        string for the life of a deployment before that was caught)."""
        result = await self._get(self._url(f"/peers/{peer_id}/card"), self.card_timeout)
        card = (result or {}).get("peer_card")
        if not isinstance(card, list):
            return []
        return [str(line).lstrip("-* ").strip() for line in card if str(line).strip()]

    async def representation(
        self,
        *,
        peer_id: str,
        search_query: str,
        max_conclusions: int = 9,
        target: str | None = None,
    ) -> Recollection:
        """What Honcho concludes about one peer. `peer_id` is the OBSERVER,
        `target` the OBSERVED; `target=None` reads the peer's own
        self-model — this IS the self-card read `render.render_self_card`
        renders. Unreachable (not merely empty) is the caller's signal to
        skip the block rather than render nothing-and-look-checked."""
        payload: dict[str, object] = {
            "search_query": search_query,
            "max_conclusions": max_conclusions,
            "search_top_k": max(1, max_conclusions // 3),
            "include_most_frequent": True,
            "search_max_distance": _MAX_DISTANCE,
        }
        if target is not None:
            payload["target"] = target
        result = await self._post(
            self._url(f"/peers/{peer_id}/representation"), payload, self.representation_timeout
        )
        if result is None:
            return Recollection(reachable=False)
        return Recollection(reachable=True, conclusions=_conclusions(result.get("representation")))
