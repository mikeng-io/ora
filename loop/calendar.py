"""Ambiguous calendar stub (design/07 item 14; design/02 §1). Stretch,
after 1-6 are green (design/04 §2's stretch row): `POST /api/calendar/
events` on a note close, with an anchor.

The request body is a documented TODO — filling it needs a logged-in
Ambiguous OpenAPI reference (Opus audit finding 29: "the schema is a login
away with no slot for it"), which is event-day work, after the demo is
green. This stub's whole job tonight is to refuse to run without a key
rather than silently no-op: a missing `AMBIGUOUS_API_KEY` is a reason to
skip the stretch, not a reason to fabricate a request.
"""

from __future__ import annotations

BASE_URL = "https://app.ambiguous.ai/api/"


class AmbiguousNotConfigured(RuntimeError):
    """Raised by `create_event` when no `AMBIGUOUS_API_KEY` is available.
    The stretch is skipped, not faked."""


async def create_event(
    api_key: str,
    *,
    title: str,
    anchor_at: str,
    anchor_place: str = "",
) -> dict:
    """`POST {BASE_URL}calendar/events` — request body TODO (see module
    docstring). Never called without a key: `AmbiguousNotConfigured` is the
    fail-closed signal event-day code checks for before attempting it."""
    if not api_key:
        raise AmbiguousNotConfigured(
            "AMBIGUOUS_API_KEY is not set — the calendar stretch is skipped, not faked"
        )
    # TODO (event-day, after 1-6 are green): the request body, filled from
    # the logged-in Ambiguous OpenAPI reference (design/03 §2). Nothing
    # below this line runs tonight.
    raise NotImplementedError(
        "the Ambiguous request body is a documented TODO — design/03 §2, event-day"
    )
