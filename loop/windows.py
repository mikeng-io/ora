"""Three small fixes to `loop.py`'s three real weaknesses: a cooldown that
survives a restart, a bounded window, and a re-entrancy guard. Pure
functions where possible; only the cooldown helpers touch the store.

Why the cooldown reads Postgres instead of memory: `RoomState.last_spoke_at`
is process memory, and a restart forgets it — Ora would happily speak twice
in a row right after a redeploy, which on stage reads as broken rather than
as "just restarted". The truth is already durable: every row Ora sends is
`messages.is_ora = TRUE`, so the cooldown is a MAX(ts) query away from
surviving anything that survives Postgres.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from loop.config import Clocks
from loop.store import Store


async def seconds_since_ora_spoke(
    store: Store, *, platform: str, conversation_id: str, now: datetime
) -> float | None:
    """Age, in seconds, of the newest `is_ora` row in this room; `None`
    when Ora has never spoken there.

    A raw read — it can raise on a store failure, same as `_newest_row_id`
    and friends in `loop.py`. Callers on the hot path should go through
    `in_cooldown`, which is the one that never raises; this one stays
    honest about the fact that it hit the network, for callers (tests,
    a future admin command) that want the real number or the real error.
    """
    newest_ts = await store.fetchval(
        """SELECT MAX(ts) FROM messages
            WHERE platform = $1 AND conversation_id = $2 AND is_ora = TRUE""",
        platform,
        conversation_id,
    )
    if newest_ts is None:
        return None
    return (now - newest_ts).total_seconds()


@dataclass(frozen=True)
class CooldownStatus:
    """`active` is the decidable signal: True means don't speak.

    `age_seconds` is None only when Ora has never spoken in the room —
    never as a stand-in for "unknown due to error"; that case is carried
    explicitly by `errored`. `remaining_seconds` is 0.0 whenever `active`
    is False, so a caller can always print "cooldown, Ns left" without a
    None-check.
    """

    active: bool
    age_seconds: float | None
    remaining_seconds: float
    errored: bool = False


async def in_cooldown(
    store: Store, clocks: Clocks, *, platform: str, conversation_id: str, now: datetime
) -> CooldownStatus:
    """The fail-closed wrapper around `seconds_since_ora_spoke`. Never
    raises into the loop.

    Which way is safe: on stage, staying quiet when Ora could have spoken
    is invisible — the room never learns a turn was skipped. Speaking when
    she should have stayed quiet (because the store lied about the age, or
    a crash reset state right after she spoke) is a double-post, visible to
    everyone in the room, in the middle of a demo. So a store failure here
    degrades to "assume the worst": treat it as if she just spoke, i.e. a
    full cooldown window, `errored=True` so the caller's log line can say
    why. This matches the repo's own rule (CLAUDE.md: "fail closed
    everywhere; silence is the default").
    """
    try:
        age = await seconds_since_ora_spoke(
            store, platform=platform, conversation_id=conversation_id, now=now
        )
    except Exception:  # noqa: BLE001 — a cooldown check must not kill a room pass
        return CooldownStatus(
            active=True,
            age_seconds=None,
            remaining_seconds=float(clocks.cooldown_seconds),
            errored=True,
        )

    if age is None:
        return CooldownStatus(active=False, age_seconds=None, remaining_seconds=0.0)

    remaining = clocks.cooldown_seconds - age
    if remaining <= 0:
        return CooldownStatus(active=False, age_seconds=age, remaining_seconds=0.0)
    return CooldownStatus(active=True, age_seconds=age, remaining_seconds=remaining)


def bound_window(row_ids: list[int], max_messages: int) -> list[int]:
    """Keep the most recent `max_messages` ids, ordering preserved.

    Drops from the HEAD, keeps the TAIL: a gate judgement is about what the
    room is doing *now*, and the newest rows are what "now" means. An
    over-long burst (forty messages while Ora was busy) should be judged on
    its last few, not its first few — the first few are the part of the
    conversation that has already moved on.
    """
    if max_messages <= 0:
        return []
    if len(row_ids) <= max_messages:
        return list(row_ids)
    return list(row_ids[-max_messages:])


class RoomGuard:
    """One `asyncio.Lock` per room, created on first use. A pass that
    cannot acquire skips rather than waits.

    Why skip instead of queue: the room poll timer keeps firing every 2s
    while a turn can take many seconds. If a second pass queued behind the
    first, it would wake up and judge a window that is now stale — the
    room has already moved on, possibly including a tag or new burst that
    superseded whatever prompted the first pass. Judging a stale window is
    how the agent answers a conversation that isn't the one happening
    anymore. Skipping means the NEXT poll (which sees fresh rows) makes the
    call instead.
    """

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock_for(self, platform: str, conversation_id: str) -> asyncio.Lock:
        key = (platform, conversation_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @contextlib.asynccontextmanager
    async def hold(self, platform: str, conversation_id: str) -> AsyncIterator[bool]:
        """`async with guard.hold(platform, conversation_id) as acquired:`
        — `acquired` is False when another pass over the same room is
        already in flight; the caller should skip its work in that branch.
        Released on every exit, including an exception, so one failed pass
        can never wedge the room shut for the rest of the demo.
        """
        lock = self._lock_for(platform, conversation_id)
        if lock.locked():
            yield False
            return
        await lock.acquire()
        try:
            yield True
        finally:
            lock.release()
