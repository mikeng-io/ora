"""design/07 item 14: the calendar stub refuses to run without
AMBIGUOUS_API_KEY."""

import pytest

from loop.calendar import AmbiguousNotConfigured, create_event


async def test_refuses_without_a_key() -> None:
    with pytest.raises(AmbiguousNotConfigured):
        await create_event(
            "", title="Cyberport meetup", anchor_at="2026-09-13T10:00:00+08:00"
        )


async def test_with_a_key_hits_the_documented_todo() -> None:
    """A key present but the request body not yet written (event-day,
    after 1-6 are green) is a NotImplementedError, never a silent no-op or
    a fabricated request."""
    with pytest.raises(NotImplementedError):
        await create_event(
            "fake-key", title="Cyberport meetup", anchor_at="2026-09-13T10:00:00+08:00"
        )
