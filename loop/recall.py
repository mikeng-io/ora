"""The Observe/Decide adapter over `loop/memory.py`'s Honcho client.

`loop/memory.py` implements `feed` / `peer_card` / `representation` and is
live-verified against norty's Honcho — but nothing in the loop calls it, so
`render.render_self_card` / `render.render_peer_card` are always handed
empty lists and the thesis ("memory is the medium — written at Observe and
Act, read at Orient and Decide") is not yet true in code. This module is
the wiring: `remember()` writes one message at Observe, `recall_for_turn()`
reads back exactly the shapes `render_self_card`/`render_peer_card` accept.

Same house rule as `loop/memory.py`, `loop/search.py`, `loop/route.py` and
`loop/vision.py`: **never raises into the caller.** Every failure —
an exception, a timeout, or the client answering with something that is
not the expected shape — degrades to an empty result. An empty
`<self_card>`/`<peer_card>` simply does not render; a wrong one puts
fabricated history in front of a decider, which is worse than nothing
(CMS: measure, don't guess — an unreachable read is a FAIL, not a PASS
padded with fiction).

Bounded, twice over: a *count* cap (how many conclusions/facts, how many
peers) and a *length* cap (how long one line may be), because this reads
land in a prompt on the hot path of a live demo turn — an unbounded memory
read is a token-budget bug waiting for a chatty room. Every remote call
also carries its own `asyncio.wait_for` backstop here, independent of
whatever timeout the client itself enforces — the adapter must not trust
the client to protect the turn, it must protect the turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

log = logging.getLogger("ora.recall")

# Count caps: how many lines a card may carry.
DEFAULT_MAX_SELF_CONCLUSIONS = 6
DEFAULT_MAX_PEER_FACTS = 5
DEFAULT_MAX_PRESENT = 8

# Length cap: how long one conclusion/fact line may be before truncation.
DEFAULT_MAX_FACT_CHARS = 280

# Backstop timeout for every call this module makes into the client, on
# top of (not instead of) `HonchoClient`'s own `card_timeout` /
# `representation_timeout` / `feed_timeout` — this module must not depend
# on the client it is handed actually enforcing one.
DEFAULT_TIMEOUT_SECONDS = 3.0

_PEER_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")


class Recollection(Protocol):
    reachable: bool
    conclusions: Sequence[str]


class HonchoLike(Protocol):
    """The slice of `memory.HonchoClient` this module uses. A `Protocol`
    rather than importing the concrete class: recall.py wraps the client,
    it does not need its implementation, and a test fake satisfies this
    without inheriting from anything."""

    ai_peer: str

    async def feed(self, *, peer_id: str, text: str, conversation_id: str) -> bool: ...

    async def peer_card(self, *, peer_id: str) -> list[str]: ...

    async def representation(
        self,
        *,
        peer_id: str,
        search_query: str,
        max_conclusions: int = 9,
        target: str | None = None,
    ) -> Recollection: ...


@dataclass(frozen=True)
class Recalled:
    """Exactly what `render.render_self_card` and `render.render_peer_card`
    want, so the caller passes these straight through with no reshaping:
    `self_conclusions` is `decide_turn.decide`'s own `self_card_conclusions:
    list[str]`, and `peer_facts` is its `peer_cards: list[tuple[str,
    list[str]]]` — `render_peer_card(person, facts)` for each `(person,
    facts)` pair, in the order `present` was given. A person with no
    surviving facts is simply absent from the list — the caller never
    renders an empty `<peer_card>`. `tag.handle_tag`'s single-person
    `peer_card_person`/`peer_card_facts` is the same shape narrowed to at
    most one pair: `peer_facts[0]` if `peer_facts` else `("", [])`."""

    self_conclusions: list[str]
    peer_facts: list[tuple[str, list[str]]]


def _peer_id_for(sender_label: str) -> str:
    """A Honcho-legal, *stable* peer id derived from a person's display
    name — the same name across Signal and WhatsApp, per `people.py`'s own
    rule ("the only place a human's two ids meet"), so one person feeding
    messages from either platform lands under one Honcho peer. Mirrors
    `memory._safe_conversation_key`'s fallback shape (translate, else
    hash) without importing that private helper — a different identity
    domain (peer, not session), kept as its own small pure function."""
    candidate = sender_label.strip().lower().replace(" ", "-")
    if candidate and _PEER_ID_OK.match(candidate):
        return candidate
    return "peer-" + hashlib.sha256(sender_label.encode()).hexdigest()[:16]


def _cap_lines(facts: Sequence[Any], *, max_count: int, max_chars: int) -> list[str]:
    """Stringify defensively (a garbage element is not an exception, just
    a line), drop blanks, clip long lines with a trailing ellipsis, and
    stop at `max_count`."""
    out: list[str] = []
    for raw in facts:
        text = str(raw).strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        out.append(text)
        if len(out) >= max_count:
            break
    return out


def _dedup_capped(labels: Sequence[str], *, max_present: int) -> list[str]:
    seen: list[str] = []
    for label in labels:
        cleaned = label.strip() if isinstance(label, str) else ""
        if not cleaned or cleaned in seen:
            continue
        seen.append(cleaned)
        if len(seen) >= max_present:
            break
    return seen


async def remember(
    client: HonchoLike,
    *,
    platform: str,
    conversation_id: str,
    sender_label: str,
    body: str,
    is_ora: bool,
    ts: datetime,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Feed one message into Honcho at Observe. Cheap and best-effort:
    `False` on any failure, and the ingest that called this must carry on
    exactly as if Honcho did not exist (`loop/memory.py`'s own rule — an
    outage here must not block a message from landing in Postgres, the
    authoritative record).

    `ts` is provenance the caller already has on hand, not a value Honcho
    accepts: `HonchoClient.feed` takes no timestamp (Honcho stamps its own
    receipt time, and `memory.py`'s own docstring is explicit that its
    batch-level timestamp is not a recency signal at conclusion
    granularity anyway) — kept as a parameter so a caller passing a
    `TranscriptRow`-shaped message never has to strip a field first, and
    so a future caller that wants to log lag has it without a second read.
    `platform` is likewise carried for logging/provenance; the Honcho
    session is keyed on `conversation_id` alone (`memory.ensure_session`).
    """
    if not body.strip():
        return False
    peer_id = client.ai_peer if is_ora else _peer_id_for(sender_label)
    try:
        result = await asyncio.wait_for(
            client.feed(peer_id=peer_id, text=body, conversation_id=conversation_id),
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — Honcho is never load-bearing
        log.warning(
            "recall.remember failed platform=%s conversation_id=%s ts=%s: %s",
            platform, conversation_id, ts.isoformat(), exc,
        )
        return False
    return result is True


def _as_conclusions(value: Any) -> list[str]:
    """Duck-typed, not `isinstance`-checked against `memory.Recollection`:
    a test fake or a future client need only carry `.reachable` /
    `.conclusions`. Anything else — `None`, a dict, a string, a
    `Recollection`-shaped object with `reachable=False` — is an empty
    self-card, never a guess."""
    if getattr(value, "reachable", False) is not True:
        return []
    conclusions = getattr(value, "conclusions", None)
    if not isinstance(conclusions, (list, tuple)):
        return []
    return [str(c) for c in conclusions]


async def _self_conclusions(
    client: HonchoLike, *, search_query: str, timeout_seconds: float
) -> list[str]:
    try:
        recollection = await asyncio.wait_for(
            client.representation(peer_id=client.ai_peer, search_query=search_query),
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — Honcho is never load-bearing
        log.warning("recall self-card read failed: %s", exc)
        return []
    return _as_conclusions(recollection)


async def _peer_facts(client: HonchoLike, label: str, *, timeout_seconds: float) -> list[str]:
    try:
        facts = await asyncio.wait_for(
            client.peer_card(peer_id=_peer_id_for(label)), timeout=timeout_seconds
        )
    except Exception as exc:  # noqa: BLE001 — Honcho is never load-bearing
        log.warning("recall peer-card read failed for %r: %s", label, exc)
        return []
    if not isinstance(facts, list):
        return []
    return [str(f) for f in facts]


async def recall_for_turn(
    client: HonchoLike,
    *,
    present: Sequence[str],
    self_query: str = "",
    max_self_conclusions: int = DEFAULT_MAX_SELF_CONCLUSIONS,
    max_peer_facts: int = DEFAULT_MAX_PEER_FACTS,
    max_fact_chars: int = DEFAULT_MAX_FACT_CHARS,
    max_present: int = DEFAULT_MAX_PRESENT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Recalled:
    """Read what a decider needs for one turn: the self-card conclusions
    plus one peer card per person currently in the room, shaped exactly
    for `render.render_self_card` / `render.render_peer_card`.

    `present` is the distinct sender labels in the turn's window (e.g.
    `{r.sender_label for r in transcript if not r.is_ora}`) — the same
    label `remember()` derived a peer id from, so the card fetched here is
    the one that message was fed under. Deduplicated and capped at
    `max_present` (first-seen order) before any call is made, so a large
    room does not fan out one Honcho round-trip per member every turn.

    Every read degrades independently: a self-card failure does not lose
    the peer cards and vice versa (`asyncio.gather(..., return_exceptions=
    True)`), and each already never raises on its own (see `_self_
    conclusions`/`_peer_facts`) — the `return_exceptions=True` is a second,
    redundant backstop, not the only one.
    """
    labels = _dedup_capped(present, max_present=max_present)

    results = await asyncio.gather(
        _self_conclusions(client, search_query=self_query, timeout_seconds=timeout_seconds),
        *(_peer_facts(client, label, timeout_seconds=timeout_seconds) for label in labels),
        return_exceptions=True,
    )
    self_result, peer_results = results[0], results[1:]

    self_conclusions = _cap_lines(
        self_result if isinstance(self_result, list) else (),
        max_count=max_self_conclusions,
        max_chars=max_fact_chars,
    )

    peer_facts: list[tuple[str, list[str]]] = []
    for label, result in zip(labels, peer_results, strict=True):
        capped = _cap_lines(
            result if isinstance(result, list) else (),
            max_count=max_peer_facts,
            max_chars=max_fact_chars,
        )
        if capped:
            peer_facts.append((label, capped))

    return Recalled(self_conclusions=self_conclusions, peer_facts=peer_facts)
