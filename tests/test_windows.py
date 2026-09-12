"""Fake-store tests for the three loop.py fixes: a durable cooldown, a
bounded window, and a re-entrancy guard. No real Postgres — see
tests/test_vision.py / tests/test_decisions.py for the pattern this
follows (a recording fake for the store, real asyncio for the guard).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from loop.config import Clocks
from loop.windows import (
    CooldownStatus,
    RoomGuard,
    bound_window,
    in_cooldown,
    seconds_since_ora_spoke,
)


class _FakeStore:
    """Records every `fetchval` call; returns a fixed value or raises."""

    def __init__(self, *, result: object = None, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query: str, *args: object) -> object:
        self.calls.append((query, args))
        if self._raises is not None:
            raise self._raises
        return self._result


NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


# ---- seconds_since_ora_spoke -------------------------------------------


async def test_age_computed_from_newest_is_ora_row() -> None:
    spoke_at = NOW - timedelta(seconds=42)
    store = _FakeStore(result=spoke_at)

    age = await seconds_since_ora_spoke(
        store, platform="signal", conversation_id="g1", now=NOW
    )

    assert age == pytest.approx(42.0)
    assert store.calls[0][1] == ("signal", "g1")


async def test_age_is_none_when_ora_has_never_spoken() -> None:
    store = _FakeStore(result=None)

    age = await seconds_since_ora_spoke(
        store, platform="signal", conversation_id="g1", now=NOW
    )

    assert age is None


async def test_seconds_since_ora_spoke_propagates_a_store_error() -> None:
    """The raw helper is honest about a store failure — it is
    `in_cooldown` that degrades, not this one."""
    store = _FakeStore(raises=RuntimeError("connection reset"))

    with pytest.raises(RuntimeError):
        await seconds_since_ora_spoke(
            store, platform="signal", conversation_id="g1", now=NOW
        )


# ---- in_cooldown ----------------------------------------------------------


async def test_in_cooldown_false_when_never_spoken() -> None:
    store = _FakeStore(result=None)
    clocks = Clocks(cooldown_seconds=60)

    status = await in_cooldown(
        store, clocks, platform="signal", conversation_id="g1", now=NOW
    )

    assert status == CooldownStatus(active=False, age_seconds=None, remaining_seconds=0.0)


async def test_in_cooldown_active_just_inside_the_boundary() -> None:
    """age = cooldown - 1s: strictly inside the window, must be active."""
    clocks = Clocks(cooldown_seconds=60)
    spoke_at = NOW - timedelta(seconds=59)
    store = _FakeStore(result=spoke_at)

    status = await in_cooldown(
        store, clocks, platform="signal", conversation_id="g1", now=NOW
    )

    assert status.active is True
    assert status.age_seconds == pytest.approx(59.0)
    assert status.remaining_seconds == pytest.approx(1.0)
    assert status.errored is False


async def test_in_cooldown_inactive_exactly_at_the_boundary() -> None:
    """age == cooldown_seconds exactly: the window has elapsed, not active.
    Matches the pre-existing loop.py semantics of `since < cooldown_seconds`."""
    clocks = Clocks(cooldown_seconds=60)
    spoke_at = NOW - timedelta(seconds=60)
    store = _FakeStore(result=spoke_at)

    status = await in_cooldown(
        store, clocks, platform="signal", conversation_id="g1", now=NOW
    )

    assert status.active is False
    assert status.remaining_seconds == 0.0


async def test_in_cooldown_inactive_just_past_the_boundary() -> None:
    clocks = Clocks(cooldown_seconds=60)
    spoke_at = NOW - timedelta(seconds=61)
    store = _FakeStore(result=spoke_at)

    status = await in_cooldown(
        store, clocks, platform="signal", conversation_id="g1", now=NOW
    )

    assert status.active is False


async def test_in_cooldown_degrades_to_active_on_store_error() -> None:
    """Fail closed: a store failure must never come back as "safe to
    speak" — that would risk a visible double-post on stage."""
    clocks = Clocks(cooldown_seconds=60)
    store = _FakeStore(raises=RuntimeError("connection reset"))

    status = await in_cooldown(
        store, clocks, platform="signal", conversation_id="g1", now=NOW
    )

    assert status.active is True
    assert status.errored is True
    assert status.age_seconds is None
    assert status.remaining_seconds == 60.0


# ---- bound_window ----------------------------------------------------------


def test_bound_window_keeps_newest_n_and_preserves_order() -> None:
    assert bound_window([1, 2, 3, 4, 5], 3) == [3, 4, 5]


def test_bound_window_with_fewer_than_n() -> None:
    assert bound_window([1, 2], 5) == [1, 2]


def test_bound_window_with_exactly_n() -> None:
    assert bound_window([1, 2, 3], 3) == [1, 2, 3]


def test_bound_window_with_zero_rows() -> None:
    assert bound_window([], 3) == []


def test_bound_window_with_a_huge_list() -> None:
    row_ids = list(range(10_000))
    assert bound_window(row_ids, 4) == [9996, 9997, 9998, 9999]


def test_bound_window_with_max_messages_zero() -> None:
    assert bound_window([1, 2, 3], 0) == []


# ---- RoomGuard --------------------------------------------------------------


async def test_guard_admits_one_holder_and_refuses_a_concurrent_second() -> None:
    guard = RoomGuard()
    started = asyncio.Event()
    release = asyncio.Event()
    outcomes: list[bool] = []

    async def first_pass() -> None:
        async with guard.hold("signal", "g1") as acquired:
            outcomes.append(acquired)
            started.set()
            await release.wait()

    task = asyncio.create_task(first_pass())
    await started.wait()

    async with guard.hold("signal", "g1") as acquired:
        outcomes.append(acquired)  # the second pass must be refused

    release.set()
    await task

    assert outcomes == [True, False]


async def test_guard_admits_two_different_rooms_concurrently() -> None:
    guard = RoomGuard()

    async with guard.hold("signal", "g1") as first:
        async with guard.hold("signal", "g2") as second:
            assert first is True
            assert second is True


async def test_guard_releases_on_exception_so_one_failed_pass_cannot_deadlock() -> None:
    guard = RoomGuard()

    with pytest.raises(ValueError):
        async with guard.hold("signal", "g1") as acquired:
            assert acquired is True
            raise ValueError("boom")

    async with guard.hold("signal", "g1") as acquired_again:
        assert acquired_again is True
