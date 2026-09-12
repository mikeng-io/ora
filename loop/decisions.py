"""The decision log — schema-side helpers only (design/07 item 11). The
log's LOGIC (who writes what, when) is event-day; this is insert/select
over the `decisions` table, with the one property `recent_for` exists to
hold: **the bound is applied after the writer filter.**

Why that ordering is the whole module (the reference project `decision_log.recent_for`'s
lesson): a query that takes the last N rows across every writer and only
then filters to the ones a caller asked for can return **zero** rows for a
rare writer when a busy one fills the window — a `proactive_decide` tick
every 30s would starve a `tag` row from a single tag an hour ago. Filtering
in `WHERE` before `LIMIT` (below) makes that starvation structurally
impossible rather than merely unlikely.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loop.store import Store


async def record(
    store: Store,
    *,
    workspace: str,
    writer: str,
    verdict: str,
    platform: str,
    conversation_id: str,
    note_id: str | None = None,
    reminder_id: str | None = None,
    grounded_on: str | None = None,
    reason: str = "",
    call_id: int | None = None,
    ts: datetime | None = None,
) -> int:
    """One row, one statement. `reason` is a code label, never model prose
    (DEC-179) — the caller's job, not this function's, to enforce."""
    row_id = await store.fetchval(
        """INSERT INTO decisions
           (workspace, writer, verdict, platform, conversation_id, note_id,
            reminder_id, grounded_on, reason, call_id, ts)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING id""",
        workspace,
        writer,
        verdict,
        platform,
        conversation_id,
        note_id,
        reminder_id,
        grounded_on,
        reason,
        call_id,
        ts or datetime.now(UTC),
    )
    return row_id


async def recent_for(
    store: Store,
    workspace: str,
    writers: tuple[str, ...] = (),
    *,
    limit: int = 20,
):
    """Rows for `workspace`, filtered to `writers` (empty = every writer)
    IN THE QUERY — `WHERE` runs before `LIMIT`, so the bound never starves
    a writer the filter was supposed to keep. Returned oldest-first, which
    is what `render.render_loop_decisions` expects."""
    if writers:
        rows = await store.fetch(
            """SELECT * FROM (
                 SELECT * FROM decisions
                 WHERE workspace = $1 AND writer = ANY($2)
                 ORDER BY ts DESC
                 LIMIT $3
               ) recent ORDER BY ts ASC""",
            workspace,
            list(writers),
            limit,
        )
    else:
        rows = await store.fetch(
            """SELECT * FROM (
                 SELECT * FROM decisions
                 WHERE workspace = $1
                 ORDER BY ts DESC
                 LIMIT $2
               ) recent ORDER BY ts ASC""",
            workspace,
            limit,
        )
    return rows
