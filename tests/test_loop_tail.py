"""design/07 item 15: against the throwaway Postgres with the rows from
item 3 — `_fetch` + `render_tables` must read them back without error and
carry the data (ids, ages) a demo operator needs on screen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from rich.console import Console

from loop.store import Store
from loop_tail import _fetch, render_tables

DATABASE_URL = "postgresql://ora:ora@localhost:55432/ora"


async def _connect() -> Store | None:
    try:
        return await Store.connect(DATABASE_URL)
    except Exception as exc:
        if "asyncpg" in type(exc).__module__ or isinstance(exc, OSError):
            return None
        raise


@pytest.fixture
async def store():
    s = await _connect()
    if s is None:
        pytest.skip(f"no Postgres at {DATABASE_URL} — unverified: throwaway instance not up")
    await s.apply_schema()
    yield s
    await s.execute(
        "TRUNCATE messages, standing, note, reminders, decisions, gate_log, model_calls"
    )
    await s.close()


async def test_render_tables_reads_back_one_row_per_kind(store: Store) -> None:
    now = datetime.now(UTC)

    await store.execute(
        """INSERT INTO standing (platform, conversation_id, body, body_len, updated_at)
           VALUES ('signal','g1','nothing yet', 11, $1)""",
        now,
    )
    await store.execute(
        """INSERT INTO note (id, workspace, room_label, title, closing_condition, created_at)
           VALUES ('N1','demo','Signal','Cyberport tomorrow','a time is agreed', $1)""",
        now - timedelta(minutes=5),
    )
    await store.execute(
        """INSERT INTO reminders
           (id, workspace, note_id, due_at, state, intent, intent_len, created_at)
           VALUES ('R1','demo','N1', $1, 'pending', 'raise the dinner note', 22, $1)""",
        now,
    )
    await store.execute(
        """INSERT INTO decisions
           (workspace, writer, verdict, platform, conversation_id, note_id, ts)
           VALUES ('demo','participation','spoke','whatsapp','w1','N1', $1)""",
        now,
    )

    data = await _fetch(store)
    assert len(data["standing"]) == 1
    assert len(data["notes"]) == 1
    assert len(data["reminders"]) == 1
    assert len(data["decisions"]) == 1

    tables = render_tables(data, now)
    assert len(tables) == 4

    console = Console(record=True, width=200)
    for table in tables:
        console.print(table)
    rendered = console.export_text()
    assert "N1" in rendered
    assert "R1" in rendered
    assert "Cyberport tomorrow" in rendered
    assert "5m ago" in rendered  # the note's age
