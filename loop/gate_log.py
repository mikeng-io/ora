"""The gate's verdict per window — observability ONLY (DEC-179
kept). Read by `loop_tail` and the runbook queries; **never by a decider.**
The gate is not a `decisions` writer, so a demo step's `go`/`no_go` has
nowhere else to be measured from.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loop.store import Store


async def record(
    store: Store,
    *,
    platform: str,
    conversation_id: str,
    verdict: str,
    window_rows: int,
    window_end_row_id: int,
    score: float | None = None,
    error_kind: str | None = None,
    call_id: int | None = None,
    ts: datetime | None = None,
) -> int:
    return await store.fetchval(
        """INSERT INTO gate_log
           (platform, conversation_id, verdict, score, error_kind,
            window_rows, window_end_row_id, call_id, ts)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
           RETURNING id""",
        platform,
        conversation_id,
        verdict,
        score,
        error_kind,
        window_rows,
        window_end_row_id,
        call_id,
        ts or datetime.now(UTC),
    )


async def recent(
    store: Store,
    *,
    platform: str | None = None,
    conversation_id: str | None = None,
    limit: int = 20,
):
    """Oldest-first, for `loop_tail`. Filtered in the query, same
    filter-before-bound discipline as `decisions.recent_for`."""
    if platform and conversation_id:
        rows = await store.fetch(
            """SELECT * FROM (
                 SELECT * FROM gate_log
                 WHERE platform = $1 AND conversation_id = $2
                 ORDER BY ts DESC LIMIT $3
               ) recent ORDER BY ts ASC""",
            platform,
            conversation_id,
            limit,
        )
    else:
        rows = await store.fetch(
            """SELECT * FROM (
                 SELECT * FROM gate_log ORDER BY ts DESC LIMIT $1
               ) recent ORDER BY ts ASC""",
            limit,
        )
    return rows
